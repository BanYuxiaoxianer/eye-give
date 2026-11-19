# eyegive

**Give your text-only LLM eyes — 看图 / OCR / 文档解析的本地优先桥接层**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() [![License: MIT](https://img.shields.io/badge/license-MIT-green)]() [![Deps](https://img.shields.io/badge/dependencies-0%20core-teal)]() [![Author](https://img.shields.io/badge/author-31-orange)]()

为**任何不具备视觉能力的纯文本大模型**补上眼睛：把图片变成结构化文字交回主模型推理；同样适用于自带视觉但能力受限、需要外部增强的模型。**核心零第三方依赖**（Python ≥ 3.10 标准库即可运行），可选引擎缺失时自动降级。

## 架构

```
调用方（CLI / MCP / 任意 agent skill）
   ↓
vision_core.route          ← 统一 envelope · 缓存(24h) · 来源解析(path/file:///data:，http 默认禁用防 SSRF)
   ↓ task_type 分流
   ├─ reasoning → engines_reason ：OpenAI 兼容通道链[1..n 按序降级] → 本地 Ollama 兜底
   ├─ ocr       → engines_ocr   ：rapidocr(本地) → tesseract(本地) → 视觉模型兜底 → 全无则 code=1
   └─ document  → engines_doc   ：txt/md/csv/html/docx/pptx/xlsx 零依赖解析 · pdf 走 pymupdf/pypdf
```

## 快速开始

```bash
# 1) 环境自检
python vision_cli.py --health

# 2) 配置视觉通道（二选一）：
#    a. 复制 config.example.json → ~/.config/eyegive/config.json，填任意 OpenAI 兼容端点
#    b. 完全本地：ollama pull llava（无需任何 API Key）

# 3) 用起来
python vision_cli.py 截图.png -t reasoning -q "这个报错是什么意思？"
python vision_cli.py 票据.png -t ocr --lang ch
python vision_cli.py 报告.docx -t document
```

### 注册为 MCP（opencode 示例）

```json
{ "mcp": { "eyegive": { "type": "local",
  "command": ["python", "/path/to/eyegive/mcp_server.py"], "enabled": true } } }
```

### 安装为 skill

把整个 `eyegive/` 目录放进 agent 的 skills 目录（opencode: `~/.config/opencode/skills/eyegive/`），`SKILL.md` 会在用户发图/发文档时引导 agent 自动调用。

## 统一返回 envelope

```json
{"task_type": "reasoning|ocr|document", "tool_used": "...", "code": 0,
 "error": null, "result": "...", "confidence": "high|medium|low", "metadata": {}}
```

| code | 含义 | 处置 |
|------|------|------|
| 0 | 成功 | 整合 result 进回答 |
| 1 | 视觉通道全部不可用 | 配 channels 或起 Ollama |
| 2 | 引擎未安装 | 按 metadata.hint 安装 |
| 3 | 输入问题 | 检查路径/格式/大小/allow_urls |
| 4 | 引擎执行失败 | 原样反馈 error，不编造内容 |
| 5 | 超时 | 调大 timeout 或换通道 |

## 可选增强

```bash
pip install pillow                    # 大图自动压缩（不装则原图发送）
pip install rapidocr-onnxruntime      # 本地 OCR（不装则 OCR 走视觉模型兜底）
pip install pymupdf                   # PDF 解析（不装则 PDF 提示安装，其余格式不受影响）
```

## 测试

```bash
python test_setup.py    # 生成 txt/html/docx/pptx/pdf 测试文件
python test_mcp.py      # 模拟 MCP 客户端全链路握手测试
```

## License

MIT © 2026 **31**（源码内含 author watermark）
