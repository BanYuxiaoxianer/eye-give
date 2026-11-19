"""engines_reason — 视觉理解引擎：OpenAI 兼容通道链（按序降级）→ 本地 Ollama 兜底。
author-watermark: 31

零第三方依赖：urllib + base64 发请求。Pillow 可用时对大图做压缩。
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from vision_core import (NO_CHANNEL, OK, TIMEOUT, ENGINE_FAILED, envelope)

MAX_SEND_BYTES = 9 * 1024 * 1024  # 多数 API 对 base64 后 payload 有 ~10MB 限制


def run(data: bytes, mime: str, task: str, cfg) -> dict:
    """对三个候选源按序尝试：channels 列表 → Ollama。全部失败返回 code=NO_CHANNEL。"""
    data, mime = _maybe_compress(data, mime)
    attempts = []

    for i, ch in enumerate(cfg.channels):
        name = ch.get("name") or f"channel[{i}]"
        t0 = time.time()
        out = _call_openai_compatible(ch, data, mime, task, cfg)
        out["metadata"]["channel"] = name
        out["metadata"]["elapsed_ms"] = int((time.time() - t0) * 1000)
        attempts.append({"tool": name, "code": out["code"],
                         "error": (out.get("error") or "")[:160],
                         "detail": (out["metadata"].get("error_detail") or "")[:200]})
        if out["code"] == OK:
            return out

    # Ollama 兜底
    if cfg.ollama_url:
        out = _call_ollama(cfg, data, mime, task)
        if out["code"] == OK:
            out["metadata"]["channel"] = f"ollama/{cfg.ollama_model}"
            out["metadata"]["fallback"] = True
            return out
        attempts.append({"tool": "ollama", "code": out["code"]})

    return envelope("reasoning", "reason-chain", NO_CHANNEL,
                    error="所有视觉通道不可用（channels 未配置或全部失败，且 Ollama 不可达）",
                    metadata={"attempts": attempts})


# ---------- 图片压缩（可选 Pillow） ----------

def _maybe_compress(data: bytes, mime: str) -> tuple[bytes, str]:
    """Pillow 可用且图片较大时缩到 1280px / JPEG 82；否则原样返回。"""
    try:
        import io
        from PIL import Image
    except ImportError:
        return data, mime

    if len(data) < 400_000:  # 小图不处理
        return data, mime
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 1280:
            scale = 1280 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        out = buf.getvalue()
        return (out, "image/jpeg") if len(out) < len(data) else (data, mime)
    except Exception:  # noqa: BLE001
        return data, mime


# ---------- OpenAI 兼容通道 ----------

def _call_openai_compatible(ch: dict, data: bytes, mime: str, task: str, cfg) -> dict:
    base_url = (ch.get("base_url") or "").rstrip("/")
    if not base_url:
        return envelope("reasoning", "openai-chat", NO_CHANNEL, error="通道缺少 base_url")
    url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    if len(base64.b64encode(data)) > MAX_SEND_BYTES:
        return envelope("reasoning", "openai-chat", ENGINE_FAILED, error="图片 base64 后超过发送上限")

    payload = {
        "model": ch.get("model", ""),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": task},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}},
            ],
        }],
        "max_tokens": ch.get("max_tokens", 3000),
    }
    headers = {"Content-Type": "application/json"}
    if ch.get("api_key"):
        headers["Authorization"] = f"Bearer {ch['api_key']}"

    timeout = int(ch.get("timeout", cfg.timeout_seconds))
    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        return envelope("reasoning", "openai-chat", ENGINE_FAILED,
                        error=f"HTTP {e.code} from {url}", metadata={"error_detail": detail})
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if "timed out" in str(reason).lower() or "timeout" in str(reason).lower():
            return envelope("reasoning", "openai-chat", TIMEOUT, error=f"请求超时({timeout}s): {reason}")
        return envelope("reasoning", "openai-chat", NO_CHANNEL, error=f"连接失败: {reason}")
    except Exception as e:  # noqa: BLE001
        return envelope("reasoning", "openai-chat", ENGINE_FAILED, error=f"解析响应失败: {e}")

    try:
        msg = resp["choices"][0]["message"]
        content = msg.get("content")
        if isinstance(content, list):  # 有些兼容端点返回分段 content
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = (content or "").strip()
        if not content:
            # 兼容 reasoning 模型把答案放进 reasoning_content 的情况
            content = (msg.get("reasoning_content") or "").strip()
        if not content:
            return envelope("reasoning", "openai-chat", ENGINE_FAILED,
                            error="模型返回空内容（max_tokens 太小或模型不支持视觉输入）")
        usage = resp.get("usage") or {}
        return envelope("reasoning", "openai-chat", OK, content, "high",
                        metadata={"model": ch.get("model"), "usage": usage})
    except (KeyError, IndexError, TypeError) as e:
        return envelope("reasoning", "openai-chat", ENGINE_FAILED, error=f"响应结构异常: {e}")


# ---------- Ollama 兜底 ----------

def _call_ollama(cfg, data: bytes, mime: str, task: str) -> dict:
    url = cfg.ollama_url.rstrip("/") + "/api/chat"
    payload = {
        "model": cfg.ollama_model,
        "messages": [{
            "role": "user",
            "content": task,
            "images": [base64.b64encode(data).decode()],
        }],
        "stream": False,
        "options": {"num_predict": 2048},
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=max(cfg.timeout_seconds, 180)) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        return envelope("reasoning", "ollama", ENGINE_FAILED, error=f"HTTP {e.code}",
                         metadata={"error_detail": detail,
                                   "hint": f"模型不存在时先执行: ollama pull {cfg.ollama_model}"})
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        code = TIMEOUT if "timed out" in msg.lower() else NO_CHANNEL
        return envelope("reasoning", "ollama", code, error=f"Ollama 不可用: {msg}")

    try:
        content = (resp["message"]["content"] or "").strip()
        if not content:
            return envelope("reasoning", "ollama", ENGINE_FAILED, error="Ollama 返回空内容")
        return envelope("reasoning", "ollama", OK, content, "medium", metadata={"model": cfg.ollama_model})
    except (KeyError, TypeError) as e:
        return envelope("reasoning", "ollama", ENGINE_FAILED, error=f"响应结构异常: {e}")
