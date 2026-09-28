from app.processors.base import DocumentProcessor
from app.processors.docx import DOCXProcessor
from app.processors.excel import ExcelProcessor
from app.processors.image import ImageProcessor
from app.processors.pdf import PDFProcessor
from app.storage.base import StorageService


class UnsupportedProcessorType(ValueError):
    pass


class ProcessorRouter:
    def __init__(self, storage: StorageService):
        self.storage = storage

    def get_processor(self, mime_type: str | None, document_type: str | None) -> DocumentProcessor:
        extension = (document_type or "").lower().lstrip(".")
        mime = (mime_type or "").lower().split(";", 1)[0]
        if extension == "pdf" and mime == "application/pdf":
            return PDFProcessor(self.storage)
        if extension == "docx" and mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return DOCXProcessor(self.storage)
        if extension == "xlsx" and mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return ExcelProcessor(self.storage)
        if extension == "xls" and mime in {"application/vnd.ms-excel", "application/msexcel"}:
            return ExcelProcessor(self.storage)
        image_mimes = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "tif": "image/tiff", "tiff": "image/tiff"}
        if extension in image_mimes and mime == image_mimes[extension]:
            return ImageProcessor(self.storage)
        raise UnsupportedProcessorType("No processor is available for this validated document type")
