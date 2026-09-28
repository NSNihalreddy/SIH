from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
import shutil

from PIL import Image


class OCREngineUnavailable(RuntimeError):
    pass


@dataclass
class OCRBlock:
    text: str
    bounding_box: dict[str, float]
    confidence: Decimal | None


@dataclass
class OCRResult:
    text: str
    confidence: Decimal | None
    blocks: list[OCRBlock] = field(default_factory=list)
    method: str = "tesseract"


class OCRService:
    def recognize(self, image_bytes: bytes) -> OCRResult:
        if not shutil.which("tesseract"):
            raise OCREngineUnavailable("Tesseract OCR is not installed or not available on PATH")
        try:
            import pytesseract
            from pytesseract import Output

            with Image.open(BytesIO(image_bytes)) as image:
                data = pytesseract.image_to_data(image, output_type=Output.DICT)
        except OCREngineUnavailable:
            raise
        except Exception as exc:
            raise RuntimeError("Local Tesseract OCR failed to process the image") from exc

        grouped: dict[tuple[int, int, int, int], list[int]] = {}
        for index, text in enumerate(data["text"]):
            if text.strip():
                key = (data["page_num"][index], data["block_num"][index], data["par_num"][index], data["line_num"][index])
                grouped.setdefault(key, []).append(index)

        blocks: list[OCRBlock] = []
        confidences: list[float] = []
        for indexes in grouped.values():
            words = [data["text"][index].strip() for index in indexes]
            numeric_confidence = [float(data["conf"][index]) for index in indexes if float(data["conf"][index]) >= 0]
            if numeric_confidence:
                confidence = sum(numeric_confidence) / len(numeric_confidence) / 100
                confidences.append(confidence)
            else:
                confidence = None
            left = min(data["left"][index] for index in indexes)
            top = min(data["top"][index] for index in indexes)
            right = max(data["left"][index] + data["width"][index] for index in indexes)
            bottom = max(data["top"][index] + data["height"][index] for index in indexes)
            blocks.append(OCRBlock(" ".join(words), {"x0": left, "y0": top, "x1": right, "y1": bottom}, Decimal(str(round(confidence, 4))) if confidence is not None else None))

        text = "\n".join(block.text for block in blocks)
        average_confidence = Decimal(str(round(sum(confidences) / len(confidences), 4))) if confidences else None
        return OCRResult(text=text, confidence=average_confidence, blocks=blocks)
