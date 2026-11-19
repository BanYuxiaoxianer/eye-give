"""test_setup.py — 生成测试文件：txt / html / docx / pptx / pdf。零依赖（pdf 用本机已装的 pymupdf）。"""
import io
import sys
import zipfile
from pathlib import Path

TEST_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "testdata"
TEST_DIR.mkdir(parents=True, exist_ok=True)

# 1) txt
(TEST_DIR / "note.txt").write_text("第一条：看图任务\n第二条：OCR 任务\n第三条：文档解析", encoding="utf-8")

# 2) html（含应被剔除的 script/style）
(TEST_DIR / "page.html").write_text(
    "<html><head><style>body{color:red}</style><script>bad()</script></head>"
    "<body><h1>标题</h1><p>第一段正文。</p><p>第二段正文。</p></body></html>",
    encoding="utf-8")

# 3) docx —— 手工构造最小合法 OOXML
buf = io.BytesIO()
z = zipfile.ZipFile(buf, "w")
z.writestr("[Content_Types].xml",
           '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="xml" ContentType="application/xml"/></Types>')
body = "".join(f"<w:p><w:r><w:t>段落{i}：eyegive 测试文本。</w:t></w:r></w:p>" for i in range(1, 4))
z.writestr("word/document.xml",
           '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
z.close()
(TEST_DIR / "test.docx").write_bytes(buf.getvalue())

# 4) pptx —— 两张幻灯片
buf = io.BytesIO()
z = zipfile.ZipFile(buf, "w")
z.writestr("[Content_Types].xml",
           '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="xml" ContentType="application/xml"/></Types>')
for i, texts in enumerate([(f"封面：eyegive 演示",), ("第二页：要点A", "第二页：要点B")], 1):
    tx = "".join(f"<a:t>{t}</a:t>" for t in texts)
    z.writestr(f"ppt/slides/slide{i}.xml",
               '<?xml version="1.0"?><p:sld xmlns:p="a" xmlns:a="a"><p:cSld><p:spTree>'
               f'{tx}</p:spTree></p:cSld></p:sld>')
z.close()
(TEST_DIR / "test.pptx").write_bytes(buf.getvalue())

# 5) pdf —— 用 pymupdf（没装则跳过）
try:
    import pymupdf
    d = pymupdf.open()
    d.new_page(); d[-1].insert_text((72, 72), "PDF 第一页：eyegive 测试。", fontname="china-s")
    d.new_page(); d[-1].insert_text((72, 72), "PDF 第二页：第二段内容。", fontname="china-s")
    d.save(str(TEST_DIR / "test.pdf")); d.close()
except ImportError:
    print("(pymupdf 未安装，跳过 pdf 生成)")

# 6) 一张最小 PNG（16x16 灰色）用于 OCR 输入校验测试
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z1+AAAAGklEQVR4nGP8z8DAwMDAxMDAwMDA"
           "wMDAAAAMAwD/AMHCYQAAAABJRU5ErkJggg==")
import base64
(TEST_DIR / "tiny.png").write_bytes(base64.b64decode(PNG_B64))

print(f"test files ready in {TEST_DIR}:")
for f in sorted(TEST_DIR.iterdir()):
    print(f"  {f.name:12s} {f.stat().st_size} B")
