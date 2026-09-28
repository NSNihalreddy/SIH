from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from app.models.rag import DocumentChunk

_TOKEN = re.compile(r"[a-z][a-z0-9-]{1,}", re.I)
_STOP = frozenset("a an and are as at be been but by can do for from had has have he her his in into is it its of on or our she that the their them there these they this to was were what when where which who will with would you your document page report table figure source data appendix contents section following mining department government annual year years india row rows column columns header headers qty during last first second third fourth fifth one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty wise total type".split())
_NOISE = re.compile(r"^(?:[a-z]{1,2}|[0-9]+|[a-f0-9]{12,})$", re.I)


def extract_document_intelligence(chunks: Iterable[dict], *, limit: int = 100) -> dict:
    """Deterministic TF/DF summary; all evidence references point at persisted chunks."""
    rows = list(chunks)
    term_counts: Counter[str] = Counter()
    token_counts: Counter[str] = Counter()
    doc_sets: dict[str, set[str]] = defaultdict(set)
    page_sets: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, list[dict]] = defaultdict(list)
    per_doc: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        document_id = str(row["document_id"])
        page_id = str(row["page_id"]) if row.get("page_id") else None
        source_tokens = [t.casefold() for t in _TOKEN.findall(row.get("text") or "")]
        tokens = [t for t in source_tokens if t not in _STOP and not _NOISE.fullmatch(t)]
        phrases = tokens + [f"{a} {b}" for a, b in zip(source_tokens, source_tokens[1:])
            if a != b and a not in _STOP and b not in _STOP and not _NOISE.fullmatch(a) and not _NOISE.fullmatch(b)]
        local = Counter(phrases)
        for term, count in local.items():
            # Count each term once per source chunk; overlapping chunk windows and
            # repeated table headings otherwise dominate a corpus word cloud.
            term_counts[term] += 1
            token_counts[term] += count
            doc_sets[term].add(document_id)
            if page_id:
                page_sets[term].add(page_id)
            per_doc[document_id][term] += 1
            if len(evidence[term]) < 5:
                evidence[term].append({"document_id": document_id, "document_version_id": str(row["document_version_id"]),
                    "page_id": page_id, "page_number": row.get("page_number"), "source_unit_id": str(row["source_unit_id"]) if row.get("source_unit_id") else None,
                    "chunk_id": str(row["chunk_id"]), "evidence_type": row.get("evidence_type", "SOURCE_DOCUMENT")})
    source_count = len({str(r["document_id"]) for r in rows})
    max_count = max(term_counts.values(), default=1)
    keywords = [{"term": term, "frequency": count, "chunk_frequency": count,
        "token_frequency": token_counts[term],
        "document_count": len(doc_sets[term]),
        "page_count": len(page_sets[term]), "weight": round(count / max_count, 6), "evidence": evidence[term]}
        for term, count in sorted(term_counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]
    # Topic candidates are prominent extracted two-word phrases, not model-generated labels.
    topics = [{"topic_id": term, "name": term, "frequency": count, "document_count": len(doc_sets[term]),
        "confidence": round(min(1.0, len(doc_sets[term]) / max(1, source_count)), 6),
        "evidence": evidence[term], "distribution": [{"document_id": doc_id, "frequency": per_doc[doc_id][term]}
            for doc_id in sorted(doc_sets[term])]} for term, count in
        sorted(((term, count) for term, count in term_counts.items() if " " in term), key=lambda item: (-item[1], item[0]))[:limit]]
    return {"status": "COMPLETED" if rows else "INSUFFICIENT_EVIDENCE", "source_count": source_count,
        "chunk_count": len(rows), "topics": topics, "keywords": keywords,
        "generated_at": None, "method": "deterministic term frequency and document frequency; unigram and adjacent bigram candidates"}


def compare_documents(documents: dict[str, dict]) -> dict:
    if len(documents) < 2:
        return {"status": "INSUFFICIENT_EVIDENCE", "documents": documents, "common_topics": [], "unique_topics": {}, "frequency_changes": []}
    topic_maps = {doc: {item["name"]: item["frequency"] for item in result.get("topics", [])} for doc, result in documents.items()}
    common = set.intersection(*(set(values) for values in topic_maps.values())) if topic_maps else set()
    union = set.union(*(set(values) for values in topic_maps.values())) if topic_maps else set()
    unique = {doc: sorted(set(values) - common) for doc, values in topic_maps.items()}
    changes = [{"topic": term, "frequencies": {doc: values.get(term, 0) for doc, values in topic_maps.items()}}
        for term in sorted(union)]
    return {"status": "COMPLETED", "documents": documents, "common_topics": sorted(common),
        "unique_topics": unique, "frequency_changes": changes,
        "numerical_analysis_status": "INSUFFICIENT_VERIFIED_DATA"}


def temporal_status(documents: list[dict]) -> str:
    # Uploaded/created timestamps describe ingestion, not the document's reporting period.
    return "INSUFFICIENT_TEMPORAL_DATA"
