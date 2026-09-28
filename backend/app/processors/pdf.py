from io import BytesIO
import logging

import pymupdf

from app.processors.base import ContentResult, DocumentProcessor, PageResult, ProcessingResult, TableCellResult, TableResult
from app.processors.ocr import OCRService

logger = logging.getLogger(__name__)


class PDFProcessor(DocumentProcessor):
    def __init__(self, storage, ocr: OCRService | None = None, minimum_text_characters: int = 20):
        super().__init__(storage)
        self.ocr = ocr or OCRService()
        self.minimum_text_characters = minimum_text_characters

    def process(self, storage_key, stage):
        stage("PARSING")
        data = self._read_document(storage_key)
        pages: list[PageResult] = []
        pdf = pymupdf.open(stream=data, filetype="pdf")
        with pdf:
            page_infos = []
            requires_ocr = False
            for page_index, page in enumerate(pdf):
                plain_text = page.get_text("text") or ""
                scanned = len("".join(plain_text.split())) < self.minimum_text_characters
                requires_ocr = requires_ocr or scanned
                page_infos.append((page_index, page, plain_text, scanned))

            ocr_results = {}
            if requires_ocr:
                stage("OCR")
                for page_index, page, _, scanned in page_infos:
                    if scanned:
                        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                        ocr_results[page_index] = self.ocr.recognize(pixmap.tobytes("png"))

            stage("TABLE_EXTRACTION")
            for page_index, page, plain_text, scanned in page_infos:
                logger.info("PDF page parsed page=%s ocr_required=%s", page_index + 1, scanned)
                if scanned:
                    ocr = ocr_results[page_index]
                    contents = [
                        ContentResult(
                            "ocr_text",
                            block.text,
                            bounding_box=block.bounding_box,
                            confidence=block.confidence,
                            metadata={"method": ocr.method},
                        )
                        for block in ocr.blocks
                    ]
                    page_text = ocr.text
                    page_metadata = {"width": page.rect.width, "height": page.rect.height, "extraction_method": ocr.method, "ocr_used": True, "ocr_confidence": str(ocr.confidence) if ocr.confidence is not None else None, "table_extraction_skipped": True}
                    page_tables = []
                else:
                    contents = []
                    text_dict = page.get_text("dict")
                    for block_index, block in enumerate(text_dict.get("blocks", [])):
                        if block.get("type") != 0:
                            continue
                        for line_index, line in enumerate(block.get("lines", [])):
                            for span_index, span in enumerate(line.get("spans", [])):
                                text_value = span.get("text", "")
                                if text_value.strip():
                                    contents.append(ContentResult(
                                        "text_span",
                                        text_value,
                                        bounding_box={"x0": span["bbox"][0], "y0": span["bbox"][1], "x1": span["bbox"][2], "y1": span["bbox"][3]},
                                        metadata={"block_index": block_index, "line_index": line_index, "span_index": span_index, "font": span.get("font"), "font_size": span.get("size")},
                                    ))
                    page_text = plain_text
                    page_metadata = {"width": page.rect.width, "height": page.rect.height, "extraction_method": "pymupdf", "ocr_used": False}
                    page_tables = []
                    if hasattr(page, "find_tables"):
                        found_tables = page.find_tables().tables
                        for table_index, table in enumerate(found_tables, start=1):
                            rows = table.extract()
                            cells = []
                            for row_index, row in enumerate(rows):
                                for column_index, value in enumerate(row):
                                    cells.append(TableCellResult(row_index, column_index, None if value is None else str(value), metadata={"source": "pymupdf"}))
                            page_tables.append(TableResult(
                                table_order=table_index,
                                extraction_method="pymupdf_find_tables",
                                metadata={"bbox": list(table.bbox) if getattr(table, "bbox", None) else None},
                                cells=cells,
                            ))

                pages.append(PageResult(
                    page_number=page_index + 1,
                    text=page_text,
                    metadata=page_metadata,
                    contents=contents,
                    tables=page_tables,
                ))
        return ProcessingResult(pages)
