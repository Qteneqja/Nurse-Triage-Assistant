"""Healthcare prompt namespace.

The current healthcare workflow delegates prompt construction to
src.orchestrator.prompts. This module exists so future healthcare workflow
versions can keep vertical prompt assets under verticals/healthcare.
"""

from src.orchestrator.prompts import FINALIZE_SYSTEM_PROMPT, PHASE1_TURN_SYSTEM_PROMPT

__all__ = ["FINALIZE_SYSTEM_PROMPT", "PHASE1_TURN_SYSTEM_PROMPT"]

