"""eyegive CLI — 命令行直调入口（零依赖路径）。
author-watermark: 31

用法：
  python vision_cli.py --health
  python vision_cli.py <图片路径> -t reasoning -q "这张图里有什么？"
  python vision_cli.py <图片路径> -t ocr --lang ch
  python vision_cli.py <文档路径> -t document
  python vision_cli.py --clear-cache
"""

from __future__ import annotations

import argparse
import json
import sys

# Windows 控制台中文输出防乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from vision_core import route, health, clear_cache


def main() -> int:
    ap = argparse.ArgumentParser(prog="eyegive", description="给无视觉 LLM 提供看图/OCR/文档解析的桥接工具")
    ap.add_argument("source", nargs="?", help="图片/文档路径，或 file:// / data URL（http 需开启 allow_urls）")
    ap.add_argument("-t", "--task-type", default="reasoning", choices=["reasoning", "ocr", "document"])
    ap.add_argument("-q", "--question", default="", help="对图片的提问（reasoning 的 task；OCR 可留空）")
    ap.add_argument("--lang", default="", help="OCR 语言：ch（默认）/ en")
    ap.add_argument("--no-cache", action="store_true", help="跳过读缓存，也不写缓存")
    ap.add_argument("--pretty", action="store_true", help="缩进美化 JSON 输出")
    ap.add_argument("--text-only", action="store_true", help="只输出 result 文本，不输出 envelope")
    sub = ap.add_mutually_exclusive_group()
    sub.add_argument("--health", action="store_true", help="环境探测（不识图）")
    sub.add_argument("--clear-cache", action="store_true", help="清空结果缓存")
    args = ap.parse_args()

    if args.clear_cache:
        out = clear_cache()
        print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.health:
        print(json.dumps(health(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if not args.source:
        ap.error("需要提供 source，或使用 --health / --clear-cache")

    out = route(args.task_type, args.source, args.question, args.lang, no_cache=args.no_cache)
    if args.text_only:
        print(out.get("result", "") if out.get("code") == 0 else f"[错误 code={out.get('code')}] {out.get('error')}", file=sys.stderr if out.get("code") != 0 else sys.stdout)
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if out.get("code") == 0 else out.get("code", 1)


if __name__ == "__main__":
    sys.exit(main())
