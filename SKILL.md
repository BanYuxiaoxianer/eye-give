---
name: eyegive
description: >
  视觉增强桥接：为不具备视觉能力的纯文本 LLM（任意模型）提供看图、OCR、文档解析；
  也用于增强主模型自带但受限的识图能力。
  当用户发送、粘贴或引用图片、截图、照片、图表、UI 截图、代码截图、扫描件，
  并要求描述、理解、提问、提取文字、OCR、识别票据时使用
  （例如"看看这张图""图里写了什么""OCR 这个截图"）；
  当用户要求解析/总结 docx/pptx/xlsx/pdf/html/txt 等文档内容时同样使用。
  工具：eyegive_vision（task_type=reasoning|ocr|document）、
  eyegive_health（环境探测）、eyegive_clear_cache（清缓存）。
  也可命令行直调：python vision_cli.py <source> -t <task_type> -q "<提问>"。
metadata:
  version: "1.0.0"
  author: "31"
  license: MIT
---

# eyegive skill

为无视觉能力的 LLM 提供多模态能力（本地优先）。本 skill 负责**使用引导**：触发时机、环境探测、工具调用与排错；能力本体由 Python 脚本（CLI 直调）或 MCP server 提供，两种形态共用同一核心。

## 触发时机

用户发出以下任一情况时激活：

- 提供图片/截图 + 要求理解、描述、问答 → `reasoning`
- 提供图片 + 要求提取文字 / OCR / 识别票据 → `ocr`
- 提供 docx/pptx/xlsx/pdf/html/txt 文件 + 要求解析或总结 → `document`

## 第一步：环境探测（推荐先做）

调用 `eyegive_health`（或 `python vision_cli.py --health`），按返回状态向用户反馈缺什么、给什么命令，**不要凭空猜测配置**：

| health 状态 | 反馈 |
| --- | --- |
| `vision_available=false`（channels 空且 ollama 不可达） | 二选一：① `config.json` 填 channels（任意 OpenAI 兼容端点 `base_url/api_key/model`）② `ollama pull llava` 用本地 Ollama |
| `ocr_engine.available=false` | 本地 OCR 缺失：`pip install rapidocr-onnxruntime`；或系统装 Tesseract + `pip install pytesseract`。**注意：即使没有本地 OCR，有视觉通道时 OCR 会自动走视觉模型兜底** |
| `pdf_engine.available=false` | 仅 PDF 需要：`pip install pymupdf`（docx/pptx/xlsx/html/txt 零依赖可解析） |
| `image_compress.available=false` | 可选 `pip install pillow`（大图自动压缩，不装则原图发送） |

若 `eyegive_health` 工具不存在：说明 MCP server 未注册，回退用命令行直调 `python vision_cli.py`；两者都不可用则引导用户安装本仓库。

## 任务选择

| 用户意图 | task_type | 说明 |
| --- | --- | --- |
| "看看这张图/描述内容/截图里的按钮" | `reasoning` | `task` 传用户的具体问题 |
| "提取图里的文字/OCR/识别票据" | `ocr` | `language` 默认 ch |
| "解析/总结这个文档" | `document` | docx/pptx/xlsx/html 本地解析；pdf 需 pymupdf |
| 文档含图表 | 先 `document` 全文，再对图表页截图单独 `ocr` 或 `reasoning` |

## 调用示例

```
eyegive_vision(task_type="reasoning", source="C:\\Users\\me\\截图.png", task="描述这张截图的内容，并指出按钮布局")
eyegive_vision(task_type="ocr", source="/tmp/receipt.png", language="ch")
eyegive_vision(task_type="document", source="D:\\docs\\报告.docx")

# 命令行直调（skill 目录即仓库目录时）
python vision_cli.py "C:/Users/me/截图.png" -t reasoning -q "这张报错是什么意思？"
```

## 返回值（统一 JSON envelope）

```json
{"task_type": "...", "tool_used": "...", "code": 0, "error": null,
 "result": "...", "confidence": "high|medium|low", "metadata": {...}}
```

## 排错表（code → 处置）

| code | 含义 | 处置 |
| --- | --- | --- |
| 0 | 成功 | 把 result 整合进回答 |
| 1 | 视觉通道全部不可用 | 反馈 health 的提示：配 channels 或起 Ollama |
| 2 | 引擎未安装 | 把 metadata.hint 里的 pip install 命令给用户 |
| 3 | 输入问题 | 检查路径/格式/大小；http 输入需 config `allow_urls=true` |
| 4 | 引擎执行失败 | 把 error 与 metadata.error_detail 原样反馈，不编造图片内容 |
| 5 | 超时 | 建议调大 channel 的 timeout 或换通道 |

## 反模式

- **禁止编造**：视觉调用失败时如实转述错误，绝不虚构图片内容。
- **纯文本模型勿直接"看图"**：没有视觉能力就调本工具，不要凭文件名猜测内容。
- **PDF 扫描件别用 document 硬解**：无文字层时改用 `ocr`（对单页截图）。
- **不要每次都 clear-cache**：缓存 24h 自动过期，仅在用户明确要求重新识别时用 `no_cache=true`。
