from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MAX_CONTEXT_CHARACTERS
from app.services.embeddings import embedding_configuration
from app.services.evidence import retrieve_evidence
from app.services.llm import LLMProviderError, LLMUnavailable, generate_answer, llm_configuration
from app.services.search import search_chunks

SYSTEM_PROMPT = """You are the evidence-bound DATA MINE assistant. Answer only from the supplied evidence. Do not use model knowledge as DATA MINE truth. Do not invent numerical values, geological entities, or production values. Distinguish TRUSTED_CANONICAL_DATA from SOURCE_DOCUMENT statements. Preserve uncertainty and disagreements. If evidence is insufficient, say so. Cite factual statements using only the supplied evidence labels such as [E1]. Never calculate totals, averages, percentages, distances, reserves, or other authoritative mining values; state that deterministic calculation is required."""
_CALCULATION_TERMS = re.compile(r"\b(total|sum|average|mean|percentage|percent|how many|how much|distance|reserve|growth rate|production rate)\b", re.I)
_TRUSTED_TERMS = re.compile(r"\b(verified|trusted|canonical|recorded)\b", re.I)
_DOMAIN_TERMS = re.compile(r"\b(production|mine|project|borehole|seam|block)\b", re.I)
_SOURCE_TERMS = re.compile(r"\b(source|document|report|page|text|according to)\b", re.I)
_CITATION = re.compile(r"\[(E\d+)\]")


def _requires_trusted_data(question: str) -> bool:
    if _TRUSTED_TERMS.search(question):
        return True
    return bool(_DOMAIN_TERMS.search(question) and not _SOURCE_TERMS.search(question))


async def answer_question(session: AsyncSession, question: str, top_k: int = 8, filters: dict | None = None) -> dict:
    normalized = " ".join(question.split())
    if not normalized or len(normalized) > 1000:
        raise ValueError("Question must contain 1 to 1000 characters")
    start = time.perf_counter()
    results = await search_chunks(session, normalized, top_k=top_k, filters=filters, mode="hybrid")
    trusted_question = _requires_trusted_data(normalized)
    allowed_types = (filters or {}).get("evidence_types")
    trusted_allowed = not allowed_types or "TRUSTED_CANONICAL_DATA" in allowed_types
    if trusted_question and trusted_allowed and not any(item["evidence_type"] == "TRUSTED_CANONICAL_DATA" for item in results):
        trusted_results = await search_chunks(session, normalized, top_k=top_k,
            filters={**(filters or {}), "evidence_types": ["TRUSTED_CANONICAL_DATA"]}, mode="hybrid")
        results = (trusted_results + results)[:top_k]
    elif trusted_question and trusted_allowed:
        results = sorted(results, key=lambda item: (item["evidence_type"] != "TRUSTED_CANONICAL_DATA", -item["score"], item["result_id"]))
    evidence = [await retrieve_evidence(session, item) for item in results]
    for i, item in enumerate(evidence, 1):
        item["citation_id"] = f"E{i}"
    citations = [{"citation_id": f"E{i}", "document_id": item["document_id"], "document_name": item["document_name"],
        "document_version_id": item["document_version_id"], "page_id": item["page_id"],
        "page_number": item["page_number"], "source_unit_id": item["source_unit_id"],
        "chunk_id": item["result_id"], "evidence_type": item["evidence_type"]} for i, item in enumerate(evidence, 1)]
    trusted_used = any(item["evidence_type"] == "TRUSTED_CANONICAL_DATA" for item in evidence)
    source_used = any(item["evidence_type"] == "SOURCE_DOCUMENT" for item in evidence)
    metadata = {"query_normalized": normalized, "retrieval_count": len(evidence),
        "scores": [{"result_id": item["result_id"], "semantic_score": item["semantic_score"], "lexical_score": item["lexical_score"], "score": item["score"], "evidence_type": item["evidence_type"]} for item in evidence],
        "model": llm_configuration()["model"], "provider": llm_configuration()["provider"],
        "trusted_data_used": trusted_used, "source_documents_used": source_used,
        "latency_ms": round((time.perf_counter()-start)*1000)}
    base = {"citations": citations, "evidence": evidence, "retrieval_metadata": metadata,
        "trust_metadata": {"trusted_data_used": trusted_used, "source_documents_used": source_used,
            "trusted_data_requested": bool(trusted_question),
            "trusted_data_available": trusted_used if trusted_question else None,
            "evidence_types": sorted({item["evidence_type"] for item in evidence}),
            "authoritative_source": "TRUSTED_CANONICAL_DATA and cited SOURCE_DOCUMENT evidence"}}
    if not evidence:
        return {"status": "INSUFFICIENT_EVIDENCE", "answer": "Insufficient indexed evidence is available to answer this question.", **base}
    if _CALCULATION_TERMS.search(normalized):
        return {"status": "DETERMINISTIC_CALCULATION_REQUIRED", "answer": None, **base}
    if not llm_configuration()["configured"]:
        return {"status": "LLM_UNAVAILABLE", "answer": None, **base}
    remaining = MAX_CONTEXT_CHARACTERS
    blocks = []
    for i, item in enumerate(evidence, 1):
        source = item["exact_source_text"]
        allowance = max(0, remaining)
        if not allowance:
            break
        blocks.append(f"[E{i}] TYPE={item['evidence_type']} DOCUMENT={item['document_name']} PAGE={item['page_number']}\n{source[:allowance]}")
        remaining -= min(allowance, len(source))
    prompt = f"Question: {normalized}\n\nEvidence (use no other information):\n" + "\n\n".join(blocks)
    try:
        generated = await generate_answer(SYSTEM_PROMPT, prompt)
    except LLMUnavailable:
        return {"status": "LLM_UNAVAILABLE", "answer": None, **base}
    except LLMProviderError:
        return {"status": "LLM_FAILED", "answer": None, **base}
    references = _CITATION.findall(generated)
    allowed = {f"E{i}" for i in range(1, len(blocks)+1)}
    if not references or any(reference not in allowed for reference in references):
        return {"status": "CITATION_VALIDATION_FAILED", "answer": None, **base}
    metadata["latency_ms"] = round((time.perf_counter()-start)*1000)
    return {"status": "ANSWERED", "answer": generated, **base}
