# DATA MINE

DATA MINE is an enterprise geological, mining, and production intelligence platform for the Smart India Hackathon 2026. The repository includes its PostgreSQL API foundation, document upload and local storage, Celery document processing, and deterministic structured extraction candidates.

## Planned technology stack

- **Backend:** Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, PostGIS, pgvector, Redis, Celery
- **Frontend:** React, TypeScript, Vite
- **AI:** OCR and document intelligence, embeddings, retrieval augmented generation (RAG), and LLM integration
- **GIS:** PostGIS with MapLibre or Leaflet
- **Storage:** A storage abstraction with local development storage and future MinIO/S3 compatibility

## Planned major modules

- Document ingestion and processing for PDFs, scanned PDFs, Excel, DOCX, and images
- OCR, document intelligence, and geological and mining/production data extraction
- Data validation and human verification
- Evidence based question answering with RAG
- Deterministic analytics and report generation
- GIS capabilities
- Audit and provenance
- Background workers and storage adapters

## Development stages

1–5. Establish the project, PostgreSQL/PostGIS/pgvector foundation, domain schema, document storage, and Celery document processing.
6. Extract deterministic, provenance-bearing structured candidates and provide calculation helpers.
7. Add canonical mapping proposals, explicit human verification, audit, and provenance.
8. Add authenticated domain canonicalization, trusted records, value history, and conflicts.
9+. Add RAG/LLM integration, GIS, reporting, and product UI in later stages.

Each stage is implemented and validated separately. RAG, embeddings, forecasting, GIS UI, reports, and the final dashboard remain future stages.

## Run locally

Backend (Python 3.10+):

```bash
cd backend
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The health endpoint is available at `http://127.0.0.1:8000/health`.

## Document processing on Windows

Set `REDIS_URL=redis://127.0.0.1:6379/0` in the project `.env` (this is the default). Start a Redis server in a terminal. If Docker Desktop is installed, from the project root run:

```powershell
docker compose up -d redis
```

Alternatively, if Redis is installed locally and `redis-server` is on `PATH`, run `redis-server`.

In a second PowerShell terminal, start FastAPI:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

In a third PowerShell terminal, start the Celery worker. The `solo` pool is required for this Windows development setup:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m celery -A app.workers.celery_app:celery_app worker --pool=solo --loglevel=INFO
```

If Redis is unavailable, the process endpoint returns HTTP 503 and marks the job `FAILED`; it does not report a job as queued.

## Structured extraction (Step 6)

After a Step 5 processing job reaches `COMPLETED`, run extraction with `POST /api/v1/documents/{document_id}/extract`. Inspect document candidates at `GET /api/v1/documents/{document_id}/extractions`, page candidates at `GET /api/v1/documents/{document_id}/pages/{page_id}/extractions`, a candidate at `GET /api/v1/extractions/{candidate_id}`, and validation details at `GET /api/v1/documents/{document_id}/validation-results`.

Candidates retain document version and page provenance, plus the Step 5 content/table/cell source where available. They remain `PENDING`; extraction does not verify or merge domain entities. Values without a recognized unit remain unresolved. Deterministic calculation helpers are in `backend/app/analytics/deterministic.py`.

## Canonical mapping and verification (Step 7)

Classify a bounded batch with `POST /api/v1/verification/classify`. Review candidates through the paginated `GET /api/v1/verification/queue` and `GET /api/v1/verification/candidates/{candidate_id}` endpoints. The queue accepts page/page size, verification status, classification/classification status, document/page, conflict, validation, and sort filters. Use the candidate `claim`, `approve`, `reject`, `edit-and-approve`, and `mark-unresolved` actions under `/api/v1/verification/candidates/{candidate_id}`. Provenance is available at `/provenance`; conflicts and canonical entities have paginated inspection endpoints under `/api/v1/verification/conflicts` and `/api/v1/canonical/entities`.

Canonical entities and records are created only after explicit approval. Rejected candidates remain stored, edits keep the extracted source snapshot, and each action is recorded in the verification event log.

## Trusted domain layer (Step 8)

Step 8 adds Argon2 password hashing, JWT bearer sessions, four user roles, and a one-time interactive administrator bootstrap (`python -m app.cli create-admin` from `backend`). Self-registration is limited to the `VIEWER` role. The JWT signing key is read from `JWT_SECRET_KEY`; `.env.example` contains only a placeholder. Keep the local `.env` private.

Verifier-only canonicalization stores trusted data in separate mine, project, borehole, coal block, formation, seam, measurement, production, and coordinate tables. Each trusted row retains its extraction candidate, source document/version/page/source unit, verification record, verifier, and verification time. Field history is append-only at the ORM layer. Conflicting verified values create reviewable conflict records and do not replace historical values.

Canonicalization rejects anything that is not `VERIFIED`, has no explicit approved mapping, or lacks required domain evidence. Deterministic identity currently uses normalized exact names, aliases, and an explicitly approved Step 7 canonical entity link; geographic proximity and hierarchical parent matching are not enabled yet. Production records require a reporting period and numeric value. Unknown mine/project links remain null.

Provision an administrator before verifier actions can be performed. Candidates from the real `coal_directory_1.pdf` remain pending until an authorized human verifies them; canonicalization does not change candidate status.

## Semantic search and evidence RAG (Step 9)

The existing `document_chunks` table stores stable, page/source-unit chunks and pgvector embeddings. Indexing runs as a Celery task and records `PENDING` / `PROCESSING` / `COMPLETED` / `FAILED` status. Deterministic chunks are reused on retries; only chunks without a valid vector for the configured model and dimension are embedded. Successfully committed vectors survive later batch failures, and job progress is reconciled from stored vectors when a job resumes. Document-page text, extracted content, table rows, and provenance-linked trusted canonical records are distinct chunk evidence types. Extraction candidates are never indexed as trusted evidence.

Configure `EMBEDDING_PROVIDER=gemini`, `openai`, or `openai_compatible`, plus `EMBEDDING_MODEL` and `EMBEDDING_API_KEY` in the private `.env`. Gemini uses `GEMINI_EMBEDDING_BATCH_SIZE` (default 1) and `GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS` (default 1 second); other providers use `EMBEDDING_BATCH_SIZE`. Retry behavior is bounded by `EMBEDDING_MAX_RETRIES` (default 8), `EMBEDDING_RETRY_BASE_SECONDS` (default 5), and `EMBEDDING_RETRY_MAX_SECONDS` (default 60); Gemini `Retry-After` is honored. The API base URL is configurable. The current database vector column is dimension 1536. `VECTOR_EMBEDDING_DIMENSION` must match `VECTOR_DIMENSION`; a dimensionality change requires a deliberate database migration and reindex. Reindexing supports model/provider changes at the same dimension. No default embedding key, model response, or synthetic vector is supplied.

Configure `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_API_KEY` separately for answer generation. Without an embedding provider, lexical search remains available while semantic search and RAG return an explicit embedding-unavailable result. Without an LLM provider, RAG returns retrieved evidence with `LLM_UNAVAILABLE` and no generated answer. Numerical calculations are returned to the deterministic calculation boundary rather than delegated to the LLM.

Search endpoints require an authenticated Step 8 user. `POST /api/v1/search/index/documents/{document_id}`, `POST /api/v1/search/index/versions/{version_id}`, and `POST /api/v1/search/reindex/{document_id}` require ADMIN, VERIFIER, or ANALYST. Check jobs at `GET /api/v1/search/index/status/{job_id}`. Search via `POST /api/v1/search/semantic` (hybrid vector + PostgreSQL lexical ranking) or `POST /api/v1/search/lexical`. `POST /api/v1/rag/query` returns evidence, trust metadata, and citations that originate from retrieved rows. Search audit stores the submitted query and selected evidence IDs, but not prompts or generated answers.

Frontend (Node.js):

```bash
cd frontend
npm install
npm run dev
```

Copy `.env.example` to `.env` only when local configuration is needed. The example contains placeholders and no credentials.
