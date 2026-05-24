import io
from pathlib import Path
from pypdf import PdfReader

def try_extract_title(file_bytes: bytes, filename: str) -> str:
    """尝试从 PDF 元数据提取标题，失败回退文件名。"""
    try:
        pdf = PdfReader(io.BytesIO(file_bytes))
        if pdf.metadata and pdf.metadata.title:
            title = pdf.metadata.title.strip()
            if len(title) > 2 and "untitled" not in title.lower():
                return title
    except Exception:
        pass
    return Path(filename).stem