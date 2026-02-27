"""
Protocol Retriever — RAG-lite

Loads versioned clinical protocols from protocols/v1/ and retrieves
relevant snippets based on keyword scoring + fuzzy matching.

Design:
- Deterministic and fast (no external services).
- Input: chief complaint, recent utterance, extracted entities, red-flag/rule state.
- Output: top-k protocol snippets (1–3) with ids and short excerpts.
- Failure or no match → returns empty list (never crashes the system).

Safety:
- Protocol retrieval NEVER overrides red flags or deterministic rules.
- Protocol context is supplementary information for the LLM only.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.governance.protocol_status import filter_protocols_by_governance

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Protocol:
    """A single clinical protocol loaded from disk."""
    id: str
    title: str
    keywords: List[str]
    body: str
    disposition_notes: str
    last_updated: str
    version: str

    # Pre-computed lowercase keywords for matching
    _kw_lower: List[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._kw_lower = [kw.lower() for kw in self.keywords]


@dataclass
class ProtocolSnippet:
    """A retrieved protocol excerpt returned to the caller."""
    id: str
    title: str
    version: str
    excerpt: str          # Short excerpt from body + disposition_notes
    score: float = 0.0    # Relevance score (higher = better)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_DEFAULT_PROTOCOL_DIR = Path(__file__).resolve().parent.parent.parent / "protocols" / "v1"


def load_protocols(protocol_dir: Path | str | None = None, apply_governance: bool = True) -> List[Protocol]:
    """Load all protocol JSON files from `protocol_dir`.

    Args:
        protocol_dir: Directory containing protocol .json files.
                      Defaults to <repo>/protocols/v1/.
        apply_governance: Whether to filter by governance status (Phase 3).

    Returns:
        List of Protocol objects.  Empty list on any directory-level failure.
    """
    pdir = Path(protocol_dir) if protocol_dir else _DEFAULT_PROTOCOL_DIR
    protocols: List[Protocol] = []

    if not pdir.is_dir():
        logger.warning(f"Protocol directory not found: {pdir}")
        return protocols

    # Load raw data first
    raw_data_list: list[dict] = []
    for fpath in sorted(pdir.glob("*.json")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw_data_list.append(data)
        except Exception as exc:
            logger.warning(f"Failed to load protocol from {fpath}: {exc}")

    # Apply governance filter (Phase 3)
    if apply_governance:
        raw_data_list = filter_protocols_by_governance(raw_data_list)

    for data in raw_data_list:
        try:
            protocols.append(Protocol(
                id=data["id"],
                title=data["title"],
                keywords=data.get("keywords", []),
                body=data.get("body", ""),
                disposition_notes=data.get("disposition_notes", ""),
                last_updated=data.get("last_updated", ""),
                version=data.get("version", "1.0"),
            ))
        except Exception as exc:
            logger.warning(f"Failed to parse protocol {data.get('id', '?')}: {exc}")

    logger.info(f"Loaded {len(protocols)} protocols from {pdir}")
    return protocols


# ---------------------------------------------------------------------------
# Tokenisation helpers
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase token split on non-alphanumeric boundaries."""
    return [tok for tok in _SPLIT_RE.split(text.lower()) if tok]


def _ngrams(tokens: List[str], n: int) -> List[str]:
    """Generate n-gram strings from a token list."""
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_protocol(
    protocol: Protocol,
    text_tokens: List[str],
    text_bigrams: List[str],
    text_raw_lower: str,
) -> float:
    """Score a protocol against the combined input text.

    Scoring strategy (simple, deterministic):
    1. Exact keyword match in raw text    → +3.0 per match
    2. Token overlap with keyword tokens   → +1.0 per overlapping token
    3. Bigram overlap with keyword phrases  → +2.0 per matching bigram
    4. Normalize by max possible to keep scores comparable.

    Returns a float >= 0.0 (higher = more relevant).
    """
    score = 0.0

    # 1) Exact keyword match in the raw (lowered) input text
    for kw in protocol._kw_lower:
        if kw in text_raw_lower:
            score += 3.0

    # 2) Token overlap
    kw_token_set: set[str] = set()
    for kw in protocol._kw_lower:
        kw_token_set.update(_tokenize(kw))

    input_token_set = set(text_tokens)
    overlap = kw_token_set & input_token_set
    score += len(overlap) * 1.0

    # 3) Bigram overlap
    kw_bigrams: set[str] = set()
    for kw in protocol._kw_lower:
        kw_bigrams.update(_ngrams(_tokenize(kw), 2))

    input_bigram_set = set(text_bigrams)
    bigram_overlap = kw_bigrams & input_bigram_set
    score += len(bigram_overlap) * 2.0

    return score


# ---------------------------------------------------------------------------
# Retriever class
# ---------------------------------------------------------------------------

class ProtocolRetriever:
    """Retrieves relevant protocol snippets for a given triage turn.

    Thread-safe (read-only after init). Deterministic scoring.
    """

    def __init__(
        self,
        protocols: List[Protocol] | None = None,
        protocol_dir: Path | str | None = None,
        top_k: int = 3,
        min_score: float = 2.0,
    ) -> None:
        """
        Args:
            protocols: Pre-loaded protocols. If None, loads from protocol_dir.
            protocol_dir: Directory to load from (defaults to protocols/v1/).
            top_k: Maximum number of results to return.
            min_score: Minimum score threshold to include a result.
        """
        self._protocols = protocols if protocols is not None else load_protocols(protocol_dir)
        self._top_k = top_k
        self._min_score = min_score

    @property
    def protocol_count(self) -> int:
        return len(self._protocols)

    def retrieve(
        self,
        chief_complaint: Optional[str] = None,
        recent_utterance: Optional[str] = None,
        extracted_entities: Optional[Dict] = None,
        red_flags: Optional[List[str]] = None,
        rules: Optional[List[str]] = None,
    ) -> List[ProtocolSnippet]:
        """Retrieve top-k relevant protocol snippets.

        Args:
            chief_complaint: The patient's chief complaint (most important signal).
            recent_utterance: The caller's most recent utterance text.
            extracted_entities: Dict of entities extracted so far (from intake state).
            red_flags: Currently triggered red flag IDs/descriptions.
            rules: Currently triggered rule IDs/descriptions.

        Returns:
            List of ProtocolSnippet (0 to top_k), ordered by relevance descending.
            Returns empty list on any error (fail-safe).
        """
        try:
            return self._retrieve_inner(
                chief_complaint=chief_complaint,
                recent_utterance=recent_utterance,
                extracted_entities=extracted_entities,
                red_flags=red_flags,
                rules=rules,
            )
        except Exception as exc:
            logger.error(f"Protocol retriever failed: {exc}. Returning empty.")
            return []

    def _retrieve_inner(
        self,
        chief_complaint: Optional[str],
        recent_utterance: Optional[str],
        extracted_entities: Optional[Dict],
        red_flags: Optional[List[str]],
        rules: Optional[List[str]],
    ) -> List[ProtocolSnippet]:
        """Core retrieval logic (may raise)."""
        if not self._protocols:
            return []

        # Build combined text for scoring
        parts: List[str] = []
        if chief_complaint:
            # Chief complaint gets double weight by including it twice
            parts.append(chief_complaint)
            parts.append(chief_complaint)
        if recent_utterance:
            parts.append(recent_utterance)
        if extracted_entities:
            for key, val in extracted_entities.items():
                if isinstance(val, str):
                    parts.append(val)
                elif isinstance(val, list):
                    parts.extend(str(v) for v in val)
        if red_flags:
            parts.extend(red_flags)
        if rules:
            parts.extend(rules)

        combined_text = " ".join(parts)
        if not combined_text.strip():
            return []

        combined_lower = combined_text.lower()
        tokens = _tokenize(combined_text)
        bigrams = _ngrams(tokens, 2)

        # Score all protocols
        scored: List[tuple[float, Protocol]] = []
        for proto in self._protocols:
            s = _score_protocol(proto, tokens, bigrams, combined_lower)
            if s >= self._min_score:
                scored.append((s, proto))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Build snippets
        results: List[ProtocolSnippet] = []
        for score, proto in scored[: self._top_k]:
            # Build a concise excerpt: first ~200 chars of body + disposition notes
            excerpt_parts = []
            if proto.body:
                excerpt_parts.append(proto.body[:200].rstrip())
                if len(proto.body) > 200:
                    excerpt_parts[-1] += "..."
            if proto.disposition_notes:
                excerpt_parts.append(f"Disposition guidance: {proto.disposition_notes[:150]}")
                if len(proto.disposition_notes) > 150:
                    excerpt_parts[-1] += "..."

            results.append(ProtocolSnippet(
                id=proto.id,
                title=proto.title,
                version=proto.version,
                excerpt=" | ".join(excerpt_parts),
                score=score,
            ))

        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_retriever: Optional[ProtocolRetriever] = None


def get_retriever() -> ProtocolRetriever:
    """Get or create the module-level ProtocolRetriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = ProtocolRetriever()
    return _retriever


def reset_retriever() -> None:
    """Reset the singleton (useful for testing)."""
    global _retriever
    _retriever = None
