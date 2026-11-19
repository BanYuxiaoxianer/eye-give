"""engines_ocr — OCR 引擎：本地优先，三级降级链。
author-watermark: 31

rapidocr（本地，开箱即用）→ pytesseract（需系统装 Tesseract）
→ 视觉模型 OCR（把图发给 reasoning 通道提取文字）
→ 全部不可用 code=NO_CHANNEL / ENGINE_MISSING。
"""

from __future__ import annotations

import io

from vision_core import (NO_CHANNEL, OK, ENGINE_MISSING, INPUT_ERROR, envelope)

# 图片才 OCR；纯文本类直接提示走 document
OCR_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/bmp"}


def run(data: bytes, mime: str, task: str, cfg, language: str = "ch") -> dict:
    if mime not in OCR_MIMES:
        return envelope("ocr", "ocr-router", INPUT_ERROR,
                        error=f"OCR 只接受图片输入，当前 mime={mime}（纯文本/文档请用 task_type=document）")

    # 1) RapidOCR（本地）
    out = _run_rapidocr(data, language)
    if out["code"] == OK or out["code"] != ENGINE_MISSING:
        return _decorate(out, "rapidocr")

    # 2) Tesseract（本地，需系统安装）
    out = _run_tesseract(data, language)
    if out["code"] == OK or out["code"] != ENGINE_MISSING:
        return _decorate(out, "pytesseract")

    # 3) 视觉模型兜底：把图发给 reasoning 通道，用提示词做 OCR
    from engines_reason import run as run_reason
    prompt = (task or "").strip() or \
        "请提取并逐行转写这张图片中的全部文字，保持原有排版顺序，不要翻译、不要注释、不要遗漏。只输出转写结果。"
    out = run_reason(data, mime, prompt, cfg)
    if out["code"] == OK:
        out["task_type"] = "ocr"          # 兜底透传时纠正回任务类型
        out["tool_used"] = "vision-fallback-ocr"
        out["confidence"] = "medium"   # 视觉模型 OCR 不如本地引擎精确
        out["metadata"]["fallback"] = True
    return out


def _decorate(out: dict, name: str) -> dict:
    out.setdefault("metadata", {})
    if "engine" not in out["metadata"]:
        out["metadata"]["engine"] = name
    return out


# ---------- RapidOCR ----------

def _run_rapidocr(data: bytes, language: str) -> dict:
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR  # 旧包名
        except ImportError:
            from rapidocr import RapidOCR  # 新包名
    except ImportError:
        return envelope("ocr", "rapidocr", ENGINE_MISSING)

    try:
        import numpy as np
        img = np.frombuffer(data, dtype=np.uint8)
        import cv2  # rapidocr 依赖里带 opencv
        img = cv2.imdecode(img, cv2.IMREAD_COLOR)
        if img is None:
            return envelope("ocr", "rapidocr", INPUT_ERROR, error="图片解码失败（cv2.imdecode 返回 None）")
    except ImportError:
        return envelope("ocr", "rapidocr", ENGINE_MISSING,
                        metadata={"hint": "rapidocr 安装不完整，请 pip install rapidocr-onnxruntime"})

    try:
        ocr = RapidOCR()
        result, _ = ocr(img)
        if not result:
            return envelope("ocr", "rapidocr", OK, "", "low",
                            metadata={"engine": "rapidocr", "note": "未检测到文字"})
        # result: [[box, text, score], ...] —— 按坐标 y 排序重组行
        items = sorted(result, key=lambda r: (r[0][0][1], r[0][0][0]))
        lines = [r[1] for r in items]
        avg = sum(float(r[2]) for r in result) / len(result)
        text = "\n".join(lines)
        return envelope("ocr", "rapidocr", OK, text,
                        "high" if avg > 0.85 else "medium",
                        metadata={"engine": "rapidocr", "lines": len(lines), "avg_confidence": round(avg, 3)})
    except Exception as e:  # noqa: BLE001
        return envelope("ocr", "rapidocr", 4, error=f"RapidOCR 执行失败: {e}",
                        metadata={"error_detail": str(e)})


# ---------- Tesseract ----------

_LANG_MAP = {"ch": "chi_sim+eng", "en": "eng"}

def _run_tesseract(data: bytes, language: str) -> dict:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return envelope("ocr", "pytesseract", ENGINE_MISSING)

    try:
        img = Image.open(io.BytesIO(data))
        lang = _LANG_MAP.get(language, _LANG_MAP["en"])
        text = pytesseract.image_to_string(img, lang=lang)
        if not text.strip():
            return envelope("ocr", "pytesseract", OK, "", "low",
                            metadata={"engine": "pytesseract", "note": "未检测到文字"})
        return envelope("ocr", "pytesseract", OK, text.strip(), "medium",
                        metadata={"engine": "pytesseract", "lang": lang})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "tesseract is not installed" in msg.lower() or "TesseractNotFoundError" in msg:
            return envelope("ocr", "pytesseract", ENGINE_MISSING,
                            metadata={"hint": "系统需安装 Tesseract OCR（Windows: ub-mannheim.de/tesseractocr；中文包 chi_sim）"})
        return envelope("ocr", "pytesseract", 4, error=f"Tesseract 执行失败: {msg}")


# ---------- 引擎可用性（供 health 复用，避免重复探测逻辑） ----------

def available() -> list:
    names = []
    try:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            import rapidocr  # noqa: F401
        names.append("rapidocr")
    except ImportError:
        pass
    try:
        import pytesseract  # noqa: F401
        names.append("pytesseract")
    except ImportError:
        pass
    return names
