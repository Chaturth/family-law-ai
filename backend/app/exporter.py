import io
from docx import Document
from docx.shared import Pt
from .config import settings


def to_docx(title, body, status="DRAFT - pending advocate review"):
    doc = Document()
    doc.styles["Normal"].font.name = "Times New Roman"
    doc.styles["Normal"].font.size = Pt(12)
    sec = doc.sections[0]
    sec.header.paragraphs[0].text = f"{status}  |  {settings.DISCLAIMER}"
    doc.add_heading(title, level=1)
    for line in body.splitlines():
        s = line.rstrip()
        if s.startswith("### "):
            doc.add_heading(s[4:], level=3)
        elif s.startswith("## "):
            doc.add_heading(s[3:], level=2)
        elif s.startswith("# "):
            doc.add_heading(s[2:], level=1)
        elif not s:
            doc.add_paragraph("")
        else:
            p = doc.add_paragraph()
            parts = s.split("**")
            for i, part in enumerate(parts):
                r = p.add_run(part)
                r.bold = i % 2 == 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
