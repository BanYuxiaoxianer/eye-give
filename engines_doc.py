"""engines_doc — 文档解析引擎：零依赖优先，可选增强。
author-watermark: 31

txt/md/csv/html/docx/pptx/xlsx → 纯标准库解析（zipfile + 正则 + html.parser）
pdf → pymupdf（首选）→ pypdf（降级）；都没有 code=ENGINE_MISSING。
"""

from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from vision_core import (OK, ENGINE_MISSING, INPUT_ERROR, envelope)

_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".xml", ".ini", ".yaml", ".yml"}


def run(data: bytes, mime: str, source: str, task: str, cfg) -> dict:
    """source 传原始字符串用于按扩展名分流。task 可指定关注点（作为 metadata 记录）。"""
    name = Path(source).name if not source.startswith(("data:", "http")) else ""
    ext = Path(name).suffix.lower()

    meta = {"file": name, "mime": mime, "ext": ext or None}
    if task:
        meta["task_focus"] = task

    # 1) 已知纯文本扩展名（text/html 要走 HTML 解析，放在 text/ 泛匹配之前）
    if ext in _TEXT_EXTS:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", "replace")
        return envelope("document", "text-reader", OK, text, "high", metadata=meta)

    # 2) HTML
    if ext == ".html" or ext == ".htm" or mime == "text/html":
        return _html(data, meta)

    # 3) 其余 text/*（无扩展名的纯文本等）
    if mime and mime.startswith("text/"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gbk", "replace")
        return envelope("document", "text-reader", OK, text, "high", metadata=meta)

    # 3) docx / pptx / xlsx（OOXML = zip + XML，零依赖解析）
    if ext == ".docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _ooxml(data, meta, kind="docx")
    if ext == ".pptx" or mime == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        return _ooxml(data, meta, kind="pptx")
    if ext == ".xlsx" or mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return _ooxml(data, meta, kind="xlsx")

    # 4) PDF（需要可选依赖）
    if ext == ".pdf" or mime == "application/pdf":
        return _pdf(data, meta)

    return envelope("document", "doc-router", INPUT_ERROR,
                    error=f"不支持的文档类型: {name or mime}（支持 txt/md/csv/html/docx/pptx/xlsx/pdf）", metadata=meta)


# ---------- HTML ----------

class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._buf, self._skip_depth = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"):
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self._buf.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._buf)).strip()


def _html(data: bytes, meta: dict) -> dict:
    try:
        parser = _HTMLText()
        parser.feed(data.decode("utf-8", "replace"))
        text = parser.text()
        if not text:
            return envelope("document", "html-parser", OK, "", "low",
                            metadata={**meta, "note": "未提取到正文文本"})
        return envelope("document", "html-parser", OK, text, "high", metadata=meta)
    except Exception as e:  # noqa: BLE001
        return envelope("document", "html-parser", 4, error=f"HTML 解析失败: {e}", metadata=meta)


# ---------- OOXML（docx/pptx/xlsx） ----------

_W_T = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
_A_T = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SS = re.compile(r"<si>(.*?)</si>", re.S)
_CELL_V = re.compile(r"<t(?:\s[^>]*)?>(.*?)</t>", re.S)


def _ooxml(data: bytes, meta: dict, kind: str) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return envelope("document", f"{kind}-zip", INPUT_ERROR,
                        error=f"不是合法的 {kind} 文件（zip 结构损坏）", metadata=meta)

    try:
        if kind == "docx":
            xml = zf.read("word/document.xml").decode("utf-8", "replace")
            paras = [_strip(x) for x in re.findall(r"<w:p[\s>].*?</w:p>|<w:p/>", xml, re.S)]
            text = "\n".join(p for p in paras if p)
            pages = None
        elif kind == "pptx":
            slides = sorted([n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                            key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
            parts = []
            for sn in slides:
                xml = zf.read(sn).decode("utf-8", "replace")
                lines = [_strip(x) for x in _A_T.findall(xml)]
                lines = [l for l in lines if l]
                if lines:
                    idx = re.search(r"slide(\d+)", sn).group(1)
                    parts.append(f"--- Slide {idx} ---\n" + "\n".join(lines))
            text = "\n\n".join(parts)
            pages = len(slides)
        else:  # xlsx —— sharedStrings 优先，否则直接抽 cell 内联文本
            try:
                ss = zf.read("xl/sharedStrings.xml").decode("utf-8", "replace")
                strings = [_strip(m) for m in _SS.findall(ss)]
            except KeyError:
                strings = []
            sheets = sorted([n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)],
                            key=lambda n: int(re.search(r"sheet(\d+)", n).group(1)))
            parts = []
            for i, sh in enumerate(sheets, 1):
                xml = zf.read(sh).decode("utf-8", "replace")
                rows = []
                for row_xml in re.findall(r"<row[\s>].*?</row>", xml, re.S):
                    cells = []
                    for c in re.findall(r"<c\b.*?</c>", row_xml, re.S):
                        m = _CELL_V.search(c)
                        if not m:
                            continue
                        if 't="s"' in c and strings:      # 共享字符串索引
                            try:
                                idx = int(re.search(r"<v>(\d+)</v>", c).group(1))
                                cells.append(strings[idx] if idx < len(strings) else "")
                                continue
                            except Exception:  # noqa: BLE001
                                pass
                        cells.append(_strip(m.group(1)))
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append(f"--- Sheet {i} ---\n" + "\n".join(rows))
            text = "\n\n".join(parts)
            pages = len(sheets)

        if not text.strip():
            return envelope("document", f"{kind}-zip", OK, "", "low",
                            metadata={**meta, "note": "文档内未提取到文本"})
        meta2 = {**meta, "zero_dep": True}
        if pages is not None:
            meta2["units"] = pages
        return envelope("document", f"{kind}-zip", OK, text, "high", metadata=meta2)
    except KeyError as e:
        return envelope("document", f"{kind}-zip", 4, error=f"{kind} 内部结构异常，缺少 {e}", metadata=meta)
    except Exception as e:  # noqa: BLE001
        return envelope("document", f"{kind}-zip", 4, error=f"{kind} 解析失败: {e}", metadata=meta)


def _strip(xml_fragment: str) -> str:
    import html as _html
    return _html.unescape(_TAG.sub("", xml_fragment)).strip()


# ---------- PDF ----------

def _pdf(data: bytes, meta: dict) -> dict:
    # 首选 pymupdf（import pymupdf 优先，兼容旧包名 import fitz）
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # noqa: F401 旧包名（有 FutureWarning 但可用）
        except ImportError:
            fitz = None
    if fitz is not None:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            pages = [p.get_text().strip() for p in doc]
            n = doc.page_count
            doc.close()
            text = "\n\n".join(f"--- Page {i} ---\n{t}" for i, t in enumerate(pages, 1) if t)
            if not text.strip():
                return envelope("document", "pymupdf", OK, "", "low",
                                {**meta, "note": "扫描版 PDF 无文字层，请改用 task_type=ocr 对单页截图识别"})
            return envelope("document", "pymupdf", OK, text, "high", metadata={**meta, "pages": n})
        except Exception as e:  # noqa: BLE001
            return envelope("document", "pymupdf", 4, error=f"pymupdf 解析失败: {e}", metadata=meta)

    # 降级 pypdf
    try:
        import pypdf
    except ImportError:
        return envelope("document", "pdf-router", ENGINE_MISSING,
                        metadata={**meta, "hint": "pip install pymupdf（推荐）或 pypdf"})
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
        text = "\n\n".join(f"--- Page {i} ---\n{t}" for i, t in enumerate(pages, 1) if t)
        if not text.strip():
            return envelope("document", "pypdf", OK, "", "low",
                            {**meta, "note": "扫描版 PDF 无文字层，请改用 task_type=ocr"})
        return envelope("document", "pypdf", OK, text, "medium", metadata={**meta, "pages": len(reader.pages)})
    except Exception as e:  # noqa: BLE001
        return envelope("document", "pypdf", 4, error=f"pypdf 解析失败: {e}", metadata=meta)
