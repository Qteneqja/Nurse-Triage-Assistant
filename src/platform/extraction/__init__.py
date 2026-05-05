"""Post-call extraction interfaces."""

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.schemas import ExtractionResult
from src.platform.extraction.service import (
    ExtractionService,
    get_extraction_service,
)

__all__ = [
    "BaseExtractionAgent",
    "ExtractionResult",
    "ExtractionService",
    "get_extraction_service",
]
