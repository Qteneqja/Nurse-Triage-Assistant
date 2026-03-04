"""
Shared Data Models — Canonical Only

All legacy types (DispositionType with SAFE/PCP/EMERGENCY,
IntakeStateB, SessionState with dual intake states) have been removed.

For the canonical disposition enum, import from src.shared.canonical.
For session state, use OrchestratorSession from src.orchestrator.schemas.
"""
from typing import Optional

from pydantic import BaseModel



class MessageRole:
    """Message roles in conversation — plain string constants."""
    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"
    PATIENT = "user"  # Alias


class SymptomItem(BaseModel):
    """Structured symptom summary."""
    name: str
    severity: str = "unknown"
    notes: Optional[str] = None
