from pathlib import PurePosixPath
import re
import unicodedata
from zipfile import BadZipFile, ZipFile

from fastapi import UploadFile


class UnsupportedDocumentType(ValueError):
    pass


ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".xls": {"application/vnd.ms-excel", "application/msexcel"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".tif": {"image/tiff", "image/tif"},
    ".tiff": {"image/tiff", "image/tif"},
}


def safe_filename(filename: str | None) -> str:
    if not filename:
        raise UnsupportedDocumentType("A filename is required")
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    extension = PurePosixPath(leaf).suffix.lower()
    if extension not in ALLOWED_MIME_TYPES:
        raise UnsupportedDocumentType("File extension is not supported")

    normalized = unicodedata.normalize("NFKD", leaf).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._-")
    stem = PurePosixPath(normalized).stem.strip("._-") or "document"
    return f"{stem[:160]}{extension}"


def _validate_signature(upload: UploadFile, extension: str) -> None:
    file_obj = upload.file
    current_position = file_obj.tell()
    try:
        file_obj.seek(0)
        header = file_obj.read(8192)
        if extension == ".pdf" and not header.startswith(b"%PDF-"):
            raise UnsupportedDocumentType("File content does not match its extension")
        if extension == ".png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UnsupportedDocumentType("File content does not match its extension")
        if extension in (".jpg", ".jpeg") and not header.startswith(b"\xff\xd8\xff"):
            raise UnsupportedDocumentType("File content does not match its extension")
        if extension in (".tif", ".tiff") and not header.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
            raise UnsupportedDocumentType("File content does not match its extension")
        if extension == ".xls" and not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise UnsupportedDocumentType("File content does not match its extension")
        if extension in (".docx", ".xlsx"):
            file_obj.seek(0)
            try:
                with ZipFile(file_obj) as archive:
                    names = set(archive.namelist())
            except (BadZipFile, OSError) as exc:
                raise UnsupportedDocumentType("Office document container is invalid") from exc
            expected_entry = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
            if "[Content_Types].xml" not in names or expected_entry not in names:
                raise UnsupportedDocumentType("File content does not match its extension")
    finally:
        file_obj.seek(current_position)


def validate_upload(upload: UploadFile) -> tuple[str, str, str]:
    filename = safe_filename(upload.filename)
    extension = PurePosixPath(filename).suffix.lower()
    mime_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if mime_type not in ALLOWED_MIME_TYPES[extension]:
        raise UnsupportedDocumentType("MIME type is not supported for this file extension")
    _validate_signature(upload, extension)
    return filename, extension.lstrip("."), mime_type
