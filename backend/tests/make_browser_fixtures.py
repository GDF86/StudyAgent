"""Create tiny real PDF/DOCX/TXT/MD fixtures for browser upload tests."""
from pathlib import Path
import pymupdf
from docx import Document

directory = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixtures"
directory.mkdir(parents=True, exist_ok=True)
with pymupdf.open() as pdf:
    pdf.new_page().insert_text((60, 60), "StudyAgent PDF ingestion: page one.")
    pdf.new_page().insert_text((60, 60), "StudyAgent PDF ingestion: page two.")
    pdf.save(directory / "学习资料.pdf")
doc = Document()
doc.add_paragraph("这是用于浏览器测试的 DOCX 学习资料。")
doc.add_table(rows=1, cols=1).cell(0, 0).text = "知识表格内容"
doc.save(directory / "课程笔记.docx")
(directory / "中文笔记.txt").write_text("中文学习资料与文本解析。\n" * 100, encoding="utf-8")
(directory / "研究记录.md").write_text("# 研究记录\n\n- 保留 Markdown 内容\n- 文本分片与向量持久化", encoding="utf-8")
with pymupdf.open() as pdf:
    pdf.new_page().insert_text((60, 60), "ALPHA source on page one: learning with verified material.")
    pdf.new_page().insert_text((60, 60), "BETA source on page two: a different knowledge point.")
    pdf.save(directory / "问答课程.pdf")
print(f"Created browser fixtures: {directory}")
