"""Post-call enrichment layer (shadow mode).

LLM-powered processing of FINALIZED Birchwood call records — never the live
call path. Master-gated by ENRICHMENT_ENABLED (default false); fails closed
and silent; the source record is never modified by enrichment.
"""
