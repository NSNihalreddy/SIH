from app.processors.base import ContentResult, DocumentProcessor, PageResult, ProcessingResult
from app.processors.ocr import OCRService


class ImageProcessor(DocumentProcessor):
    def __init__(self, storage, ocr: OCRService | None = None):
        super().__init__(storage)
        self.ocr = ocr or OCRService()

    def process(self, storage_key, stage):
        stage("OCR")
        data = self._read_document(storage_key)
        result = self.ocr.recognize(data)
        contents = [ContentResult("ocr_text", block.text, bounding_box=block.bounding_box, confidence=block.confidence, metadata={"method": result.method}) for block in result.blocks]
        return ProcessingResult([
            PageResult(
                page_number=None,
                text=result.text or None,
                metadata={"source_unit": "image", "ocr_method": result.method, "ocr_confidence": str(result.confidence) if result.confidence is not None else None, "pagination_available": False},
                contents=contents,
            )
        ])
