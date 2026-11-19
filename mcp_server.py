"""eyegive MCP server — 手写 stdio JSON-RPC，零依赖，不依赖官方 mcp SDK。
author-watermark: 31

注册方式（opencode.json）：
  { "mcp": { "eyegive": { "type": "local", "command": ["python", "<本目录>/mcp_server.py"], "enabled": true } } }

暴露工具：
  eyegive_vision      (task_type, source, task, language, no_cache)
  eyegive_health      ()
  eyegive_clear_cache ()

协议核心：line-delimited JSON-RPC 2.0 over stdin/stdout，日志一律走 stderr。
"""

from __future__ import annotations

import json
import sys

from vision_core import route, health, clear_cache, WATERMARK

PROTOCOL_VERSION = "2024-11-05"

TOOLS_SPEC = [
    {
        "name": "eyegive_vision",
        "description": ("视觉增强主入口：把图片变成文字描述交回无视觉主模型。"
                        "task_type=reasoning 看图问答 / ocr 图片文字提取 / document 文档解析"
                        "（docx/pptx/xlsx/html 零依赖，pdf 需 pymupdf）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {"type": "string", "enum": ["reasoning", "ocr", "document"],
                               "description": "看图问答=reasoning；提文字=ocr；解析文档=document"},
                "source": {"type": "string",
                            "description": "本地路径 / file:// / data URL（http 需 allow_urls）"},
                "task": {"type": "string", "description": "reasoning 的提问，可空；ocr 可指定关注区域"},
                "language": {"type": "string", "description": "OCR 语言 ch/en，默认 ch"},
                "no_cache": {"type": "boolean", "description": "跳过缓存，默认 false"},
            },
            "required": ["task_type", "source"],
        },
    },
    {
        "name": "eyegive_health",
        "description": "环境探测：视觉通道/Ollama/OCR/文档引擎配置状态（不含密钥），先调它再反馈用户缺什么。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "eyegive_clear_cache",
        "description": "清除本地结果缓存。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result(req_id, value):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": value}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(req_id, code, message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": code, "message": message}}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _tool_result(content_text):
    return {"content": [{"type": "text", "text": content_text}]}


def handle(msg: dict) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        client_pv = (msg.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION)
        _result(req_id, {
            "protocolVersion": client_pv if isinstance(client_pv, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "eyegive", "version": "1.0.0", "watermark": WATERMARK},
        })
    elif method == "notifications/initialized":
        pass  # 通知无响应
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS_SPEC})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            if name == "eyegive_vision":
                out = route(args.get("task_type", "reasoning"), args.get("source", ""),
                            args.get("task", ""), args.get("language", ""),
                            no_cache=bool(args.get("no_cache")))
            elif name == "eyegive_health":
                out = health()
            elif name == "eyegive_clear_cache":
                out = clear_cache()
            else:
                _error(req_id, -32602, f"unknown tool: {name}")
                return
            _result(req_id, _tool_result(json.dumps(out, ensure_ascii=False)))
        except Exception as e:  # noqa: BLE001
            _error(req_id, -32603, f"internal error: {e}")
    elif req_id is not None:
        _error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.stderr.write("[eyegive-mcp] stdio server started\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[eyegive-mcp] 非法 JSON 行: {line[:120]}\n")
            continue
        if not isinstance(msg, dict):
            continue
        try:
            handle(msg)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[eyegive-mcp] handler 异常: {e}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
