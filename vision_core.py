"""eyegive — 给无视觉能力的 LLM 提供 看图/OCR/文档解析 的本地优先桥接层。
author-watermark: 31

核心模块：配置加载、来源解析、缓存、统一 envelope、任务路由、health 自检。
零第三方依赖（标准库 only）。可选引擎（Pillow/rapidocr/pymupdf 等）缺失时优雅降级。

错误码（与 SKILL.md 排错表一一对应）：
  0 OK                成功
  1 NO_CHANNEL        视觉通道全部不可用（含 Ollama 兜底失败/未配置）
  2 ENGINE_MISSING    引擎未安装，需 pip install 对应 extra
  3 INPUT_ERROR       输入有问题：文件不存在/格式不支持/超过大小限制/URL 被禁
  4 ENGINE_FAILED     引擎装了但执行失败
  5 TIMEOUT           超时
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

VERSION = "1.0.0"
WATERMARK = "31"   # author watermark
APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "eyegive"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "eyegive"

# ---------- 错误码 ----------
OK = 0
NO_CHANNEL = 1
ENGINE_MISSING = 2
INPUT_ERROR = 3
ENGINE_FAILED = 4
TIMEOUT = 5

ERROR_TABLE = {
    OK: "成功",
    NO_CHANNEL: "视觉通道全部不可用：配置 channels（OpenAI 兼容端点）或启动本地 Ollama",
    ENGINE_MISSING: "引擎未安装：按提示 pip install 对应依赖后重试",
    INPUT_ERROR: "输入问题：检查文件路径/格式/大小，http(s) 输入需开启 allow_urls",
    ENGINE_FAILED: "引擎执行失败：查看 metadata.error_detail 定位原因",
    TIMEOUT: "超时：调大 timeout_seconds 或换更快的通道/引擎",
}

TASK_TYPES = ("reasoning", "ocr", "document")

# ---------- envelope ----------

def envelope(task_type: str, tool_used: str, code: int, result: str = "",
             confidence: str = "medium", error: str | None = None,
             metadata: dict | None = None) -> dict:
    """所有工具统一返回该 JSON envelope。"""
    return {
        "task_type": task_type,
        "tool_used": tool_used,
        "code": code,
        "error": error if error is not None else ERROR_TABLE.get(code, "未知错误") if code != OK else None,
        "result": result,
        "confidence": confidence,
        "metadata": metadata or {},
    }


# ---------- 配置 ----------

@dataclasses.dataclass
class Config:
    channels: list = dataclasses.field(default_factory=list)   # [{name?, base_url, api_key, model, timeout?}]
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llava"
    cache_ttl_seconds: int = 86400       # 0 = 关闭缓存
    cache_dir: str = str(DEFAULT_CACHE_DIR)
    max_input_mb: int = 50
    allow_urls: bool = False             # 防 SSRF，默认只接受本地路径 / file:// / data:
    timeout_seconds: int = 120           # 单次视觉请求默认超时
    ocr_lang: str = "ch"

    @classmethod
    def load(cls) -> "Config":
        raw = {}
        path = _find_config_path()
        if path:
            try:
                raw = json.loads(Path(path).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[eyegive] 配置文件解析失败({path}): {e}", file=sys.stderr)
        cfg = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__ and v is not None})
        # 环境变量覆盖
        if os.environ.get("EYEGIVE_OLLAMA_URL"):
            cfg.ollama_url = os.environ["EYEGIVE_OLLAMA_URL"]
        if os.environ.get("EYEGIVE_OLLAMA_MODEL"):
            cfg.ollama_model = os.environ["EYEGIVE_OLLAMA_MODEL"]
        if os.environ.get("EYEGIVE_ALLOW_URLS") in ("1", "true", "True"):
            cfg.allow_urls = True
        return cfg


def _find_config_path() -> Path | None:
    candidates = []
    if os.environ.get("EYEGIVE_CONFIG"):
        candidates.append(Path(os.environ["EYEGIVE_CONFIG"]))
    candidates += [
        DEFAULT_CONFIG_DIR / "config.json",
        APP_DIR / "config.json",
        APP_DIR.parent / "config.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def config_status(cfg: Config) -> dict:
    """health 用：报状态不报密钥。"""
    path = _find_config_path()
    return {
        "config_file": str(path) if path else None,
        "hint_set_config": "" if path else f"复制 config.example.json 到 {DEFAULT_CONFIG_DIR / 'config.json'} 并填入通道",
        "channels": [
            {"name": c.get("name", f"channel{i}"), "base_url": c.get("base_url", ""),
             "model": c.get("model", ""), "has_key": bool(c.get("api_key"))}
            for i, c in enumerate(cfg.channels)
        ],
        "ollama": {"url": cfg.ollama_url, "model": cfg.ollama_model, "reachable": _ollama_reachable(cfg)},
        "cache": {"ttl_seconds": cfg.cache_ttl_seconds, "dir": cfg.cache_dir},
        "input_limits": {"max_input_mb": cfg.max_input_mb, "allow_urls": cfg.allow_urls},
    }


def _ollama_reachable(cfg: Config) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(cfg.ollama_url.rstrip("/") + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


# ---------- 来源解析 ----------

SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

def resolve_source(source: str, cfg: Config) -> tuple[bytes, str, str]:
    """把 source（本地路径 / file:// / data URL / http(s)，按配置）解析为 (bytes, mime, cache_key)。

    抛 SourceError 时由 route() 转成 envelope(code=INPUT_ERROR)。
    """
    s = source.strip()
    if not s:
        raise _SourceError("source 为空")

    # data URL
    if s.startswith("data:"):
        try:
            head, b64 = s.split(",", 1)
            mime = head[5:].split(";")[0] or "image/png"
            data = base64.b64decode(b64)
        except Exception:
            raise _SourceError("data URL 解析失败")
        return _check_size(data, cfg), mime, "data:" + hashlib.sha1(data).hexdigest()

    # file://
    if s.startswith("file://"):
        s = s[7:]
        if os.name == "nt" and s.startswith("/"):
            s = s[1:]

    # http(s)
    if re_http := (s.startswith("http://") or s.startswith("https://")):
        if not cfg.allow_urls:
            raise _SourceError("http(s) 输入默认禁用（防 SSRF）；确需请在 config 设 allow_urls=true")
        import urllib.request
        try:
            with urllib.request.urlopen(s, timeout=30) as r:
                data = r.read(cfg.max_input_mb * 1024 * 1024 + 1)
                mime = r.headers.get_content_type() or "image/png"
        except Exception as e:
            raise _SourceError(f"下载失败: {e}")
        return _check_size(data, cfg), mime, s

    # 本地路径
    p = Path(s)
    if not p.is_file():
        raise _SourceError(f"文件不存在: {p}")
    data = p.read_bytes()
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return _check_size(data, cfg), mime, str(p.resolve())


def _check_size(data: bytes, cfg: Config) -> bytes:
    if len(data) > cfg.max_input_mb * 1024 * 1024:
        raise _SourceError(f"输入超过 {cfg.max_input_mb}MB 上限（实际 {len(data)/1024/1024:.1f}MB）")
    return data


class _SourceError(Exception):
    pass


# ---------- 缓存 ----------

def _cache_key(task_type: str, source_key: str, task: str, extra: str = "") -> str:
    h = hashlib.sha1(f"{task_type}|{source_key}|{task}|{extra}".encode()).hexdigest()
    return h


def cache_get(cfg: Config, key: str) -> dict | None:
    if cfg.cache_ttl_seconds <= 0:
        return None
    f = Path(cfg.cache_dir) / f"{key}.json"
    if not f.is_file():
        return None
    try:
        item = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - item.get("ts", 0) > cfg.cache_ttl_seconds:
        try:
            f.unlink()
        except OSError:
            pass
        return None
    return item.get("data")


def cache_put(cfg: Config, key: str, data: dict) -> None:
    if cfg.cache_ttl_seconds <= 0:
        return
    try:
        d = Path(cfg.cache_dir)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{key}.json"
        f.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def cache_clear(cfg: Config) -> int:
    d = Path(cfg.cache_dir)
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


# ---------- 引擎探测（供 health） ----------

def probe_engines() -> dict:
    st = {}
    # OCR
    ocr = None
    try:
        import rapidocr_onnxruntime  # noqa: F401
        ocr = "rapidocr"
    except ImportError:
        try:
            import rapidocr  # noqa: F401
            ocr = "rapidocr"
        except ImportError:
            try:
                import pytesseract  # noqa: F401
                ocr = "pytesseract"
            except ImportError:
                ocr = None
    st["ocr_engine"] = {"available": ocr is not None, "name": ocr,
                        "hint": "" if ocr else "pip install rapidocr-onnxruntime（或系统装 Tesseract + pip install pytesseract）"}
    # 文档
    pdf = None
    try:
        import pymupdf  # noqa: F401
        pdf = "pymupdf"
    except ImportError:
        try:
            import pypdf  # noqa: F401
            pdf = "pypdf"
        except ImportError:
            pdf = None
    st["pdf_engine"] = {"available": pdf is not None, "name": pdf,
                         "hint": "" if pdf else "pip install pymupdf（仅 PDF 需要；docx/pptx/xlsx/html 零依赖可解析）"}
    # Pillow（图片压缩，可选）
    try:
        import PIL  # noqa: F401
        st["image_compress"] = {"available": True, "name": "Pillow"}
    except ImportError:
        st["image_compress"] = {"available": False, "name": None,
                                 "hint": "pip install pillow（可选：大图自动压缩，不装则原图发送）"}
    return st


def health(cfg: Config | None = None) -> dict:
    cfg = cfg or Config.load()
    st = config_status(cfg)
    st.update(probe_engines())
    st["watermark"] = WATERMARK
    vision_ok = bool(cfg.channels) or st["ollama"]["reachable"]
    st["vision_available"] = vision_ok
    if not vision_ok:
        st["hint_vision"] = "二选一：① config.json 填 channels（任意 OpenAI 兼容端点）② ollama pull llava 后用本地 Ollama"
    return st


# ---------- 路由 ----------

def route(task_type: str, source: str, task: str = "", language: str = "",
          no_cache: bool = False) -> dict:
    """主入口：分流到三个引擎，任何异常都收敛为 envelope，绝不抛出。"""
    cfg = Config.load()
    task = (task or "").strip()
    language = language or cfg.ocr_lang

    if task_type not in TASK_TYPES:
        return envelope(task_type, "router", INPUT_ERROR,
                        error=f"未知 task_type: {task_type}（可选 {TASK_TYPES}）")

    # document 不读二进制进缓存 key 之前，先解析来源
    try:
        data, mime, source_key = resolve_source(source, cfg)
    except _SourceError as e:
        return envelope(task_type, "router", INPUT_ERROR, error=str(e))
    except Exception as e:  # noqa: BLE001
        return envelope(task_type, "router", INPUT_ERROR, error=f"来源解析失败: {e}")

    ckey = _cache_key(task_type, source_key, task, language if task_type == "ocr" else "")
    if not no_cache:
        hit = cache_get(cfg, ckey)
        if hit is not None:
            out = dict(hit)
            out["metadata"]["cache_hit"] = True
            return out

    if task_type == "reasoning":
        from engines_reason import run as run_reason
        if not task:
            task = "详细描述这张图片的内容。"
        out = run_reason(data, mime, task, cfg)
    elif task_type == "ocr":
        from engines_ocr import run as run_ocr
        out = run_ocr(data, mime, task, cfg, language)
    else:
        from engines_doc import run as run_doc
        out = run_doc(data, mime, source, task, cfg)

    if out.get("code") == OK:
        out.setdefault("metadata", {})
        out["metadata"]["cached_until"] = int(time.time() + cfg.cache_ttl_seconds) if cfg.cache_ttl_seconds > 0 else None
        if not no_cache:
            cache_put(cfg, ckey, out)
    return out


def clear_cache() -> dict:
    cfg = Config.load()
    n = cache_clear(cfg)
    return {"code": OK, "cleared": n, "cache_dir": cfg.cache_dir}


if __name__ == "__main__":
    # 快速自检：python vision_core.py health
    print(json.dumps(health(), ensure_ascii=False, indent=2))
