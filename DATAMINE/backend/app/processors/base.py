from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from app.storage.base import StorageService

StageCallback = Callable[[str], None]


@dataclass
class ContentResult:
    content_type: str
    text: str
    bounding_box: dict | None = None
    confidence: Decimal | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TableCellResult:
    row_index: int
    column_index: int
    raw_value: str | None
    normalized_value: str | None = None
    confidence: Decimal | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TableResult:
    table_order: int
    extraction_method: str
    confidence: Decimal | None = None
    metadata: dict = field(default_factory=dict)
    cells: list[TableCellResult] = field(default_factory=list)


@dataclass
class PageResult:
    page_number: int | None
    text: str | None
    metadata: dict = field(default_factory=dict)
    contents: list[ContentResult] = field(default_factory=list)
    tables: list[TableResult] = field(default_factory=list)


@dataclass
class ProcessingResult:
    pages: list[PageResult]


class DocumentProcessor(ABC):
    def __init__(self, storage: StorageService):
        self.storage = storage

    def _read_document(self, storage_key: str) -> bytes:
        with self.storage.download_file(storage_key) as stored:
            return stored.read()

    @abstractmethod
    def process(self, storage_key: str, stage: StageCallback) -> ProcessingResult:
        raise NotImplementedError
