"""Backfill missing trusted coordinates from already verified candidates.

From the repository root, preview with:
    python scripts/backfill_verified_coordinates.py

Apply the idempotent backfill explicitly with:
    python scripts/backfill_verified_coordinates.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.models.canonical import CanonicalRecord  # noqa: E402
from app.models.extraction import ExtractionCandidate  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.trusted import TrustedCoordinate  # noqa: E402
from app.services.canonicalization import canonicalize_candidate  # noqa: E402


def _missing_verified_coordinate_candidates():
    has_trusted_coordinate = select(TrustedCoordinate.id).where(
        TrustedCoordinate.source_candidate_id == ExtractionCandidate.id
    ).exists()
    return (
        select(ExtractionCandidate.id)
        .join(CanonicalRecord, CanonicalRecord.candidate_id == ExtractionCandidate.id)
        .where(
            ExtractionCandidate.candidate_type == "coordinate",
            ExtractionCandidate.classification == "COORDINATE",
            ExtractionCandidate.verification_status == "VERIFIED",
            ExtractionCandidate.mapping_status == "APPROVED",
            CanonicalRecord.record_type == "COORDINATE",
            CanonicalRecord.verified_by.is_not(None),
            ~has_trusted_coordinate,
        )
        .order_by(ExtractionCandidate.created_at, ExtractionCandidate.id)
    )


async def _run(apply: bool) -> int:
    async with AsyncSessionLocal() as session:
        candidate_ids = list((await session.scalars(_missing_verified_coordinate_candidates())).all())
        print(f"verified_coordinate_candidates_missing_trusted_row={len(candidate_ids)}")
        if not apply:
            print("dry_run=true; pass --apply to materialize these candidates")
            return 0

        created = 0
        skipped = 0
        failed = 0
        for candidate_id in candidate_ids:
            try:
                candidate = await session.get(ExtractionCandidate, candidate_id)
                record = await session.scalar(
                    select(CanonicalRecord).where(CanonicalRecord.candidate_id == candidate_id)
                )
                actor = await session.get(User, record.verified_by) if record and record.verified_by else None
                if candidate is None or record is None or actor is None:
                    skipped += 1
                    print(f"skip={candidate_id}: missing candidate, canonical record, or verifier")
                    continue
                result = await canonicalize_candidate(session, candidate_id, actor)
                if result["created"]:
                    created += 1
                else:
                    skipped += 1
            except Exception as exc:
                await session.rollback()
                failed += 1
                print(f"failed={candidate_id}: {type(exc).__name__}: {exc}")
        print(f"created={created} skipped={skipped} failed={failed}")
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="materialize missing trusted coordinate rows")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args.apply))
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    raise SystemExit(main())
