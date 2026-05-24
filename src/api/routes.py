"""
API Routes — Unified Safety Architecture

All triage goes through:
  SessionRepository → Orchestrator → GuardedLLM → SafetyGate

Legacy REST intake path is removed. Only endpoints:
  POST /start     — Create session, return session_id
  POST /{id}/turn — Submit user text, get orchestrator response
  GET  /{id}      — Get session state summary
  DELETE /{id}    — Delete session
"""

import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from src.storage.session_repository import get_session_repository
from src.orchestrator.orchestrator import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas (API-level, not domain-level)
# ---------------------------------------------------------------------------


class StartRequest(BaseModel):
    """Start a new triage session."""

    chief_complaint: Optional[str] = Field(default=None, max_length=500)
    call_sid: Optional[str] = Field(default=None, max_length=64)


class StartResponse(BaseModel):
    session_id: str
    message: str


class TurnRequest(BaseModel):
    """Submit patient text for one orchestrator turn."""

    user_text: str = Field(..., min_length=1, max_length=2000)


class TurnResponse(BaseModel):
    action: str  # "ask" | "escalate" | "finalize"
    message: str
    disposition: Optional[str] = None
    is_finalized: bool = False


class SessionSummary(BaseModel):
    session_id: str
    turn_count: int
    is_finalized: bool
    disposition: Optional[str] = None
    confidence: Optional[float] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/start", response_model=StartResponse, status_code=status.HTTP_201_CREATED
)
async def start_session(request: StartRequest):
    """Create a new triage session via the unified storage backend."""
    try:
        repo = get_session_repository()
        session = repo.create_session(call_sid=request.call_sid)

        if request.chief_complaint:
            session.intake_state.chief_complaint = request.chief_complaint
            repo.persist_session(session)

        return StartResponse(
            session_id=session.session_id,
            message="Session created. Submit patient text via POST /{session_id}/turn.",
        )
    except Exception as e:
        logger.error(f"[API] Error starting session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start session")


@router.post("/{session_id}/turn", response_model=TurnResponse)
async def submit_turn(session_id: str, request: TurnRequest):
    """Submit patient text and run one orchestrator turn.

    All LLM calls go through GuardedLLM → SafetyGate pipeline.
    """
    try:
        repo = get_session_repository()
        session = repo.load_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.is_finalized:
            raise HTTPException(status_code=400, detail="Session already finalized")

        orchestrator = get_orchestrator()
        result = await orchestrator.process_turn(session, request.user_text)
        repo.persist_session(session)

        disposition = None
        if session.is_finalized and session.finalize_output:
            disposition = session.finalize_output.disposition.value

        return TurnResponse(
            action=result["action"],
            message=result["message"],
            disposition=disposition,
            is_finalized=session.is_finalized,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in turn for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Turn processing failed")


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str):
    """Get current session state summary."""
    try:
        repo = get_session_repository()
        session = repo.load_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        disposition = None
        confidence = None
        if session.finalize_output:
            disposition = session.finalize_output.disposition.value
            confidence = session.finalize_output.confidence_score

        return SessionSummary(
            session_id=session.session_id,
            turn_count=session.turn_count,
            is_finalized=session.is_finalized,
            disposition=disposition,
            confidence=confidence,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error getting session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get session")


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session."""
    try:
        repo = get_session_repository()
        session = repo.load_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        repo.delete_session(session_id)
        return {"message": "Session deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error deleting session {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete session")
