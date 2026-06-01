# Project: AI-Driven Enterprise Data Migration Bridge

## Architectural Overview
This project simulates two distinct enterprise systems:
1. **ABASE (Legacy):** Minimalist, light-mode, utilitarian administrative module.
2. **GDS (Target):** Modern, dark-mode, high-performance SaaS dashboard with teal accents.

The bridge between them is an AI-powered ETL pipeline using Gemini/Claude to generate cleaning scripts, with an explicit **Human-in-the-Loop (HITL)** verification layer.

## Core Tech Stack
* **Frontend:** Next.js (App Router), Tailwind CSS.
* **Backend:** FastAPI, Pandas, `asyncpg` (SQLAlchemy).
* **Database:** Supabase (PostgreSQL).
* **AI Engine:** Google Gemini API (Dynamic ETL Scripting).

## Operational Rules & Safety (MUST FOLLOW)
1. **HITL Verification:** Before any migration is committed to the `gds_experiments` production table, the system must pause. It must output a `migration_plan.json` showing row counts and schema mapping for human approval in the UI.
2. **Staging Table Pattern:** All AI-cleaned data must first land in `gds_staging_experiments` (UNLOGGED table). Only after human approval is it moved to the `gds_experiments` production table.
3. **Tracing:** Every single file ingestion process must be assigned a `trace_id` (UUID). This ID must persist through the logs, the database audit tables, and the UI feedback loop.
4. **CORS & Networking:** Explicitly configure `CORSMiddleware` in `main.py` to allow communication between the Next.js frontend and the FastAPI backend.
5. **No Data Exposure:** LLM prompts must only contain `df.head()` and schema definitions. Never send the full file contents.

## Database Schema (Supabase/PostgreSQL)
* **`abase_legacy_users`:** id (PK), name, department.
* **`gds_users`:** gds_user_id (PK), name, role.
* **`gds_staging_experiments` (UNLOGGED):** staging_id (PK), trace_id, well_position, signal, status.
* **`gds_experiments` (PRODUCTION):** experiment_id (PK), gds_user_id (FK), trace_id, signal.

## Development Constraints
1. **Theme Separation:** Frontend components must be strictly themed. ABASE is `bg-slate-50 text-black`. GDS is `bg-slate-900 text-teal-400`.
2. **Idempotency:** All DB insertions must be `UPSERT`. Use `INSERT ... ON CONFLICT (trace_id) DO UPDATE`.
3. **Error Logging:** If the AI script generation fails, the system must create a log entry in `migration_audit_log` with the `trace_id` and the specific Python error traceback.

## Execution Priority (Step-by-Step)
1. **Backend Foundation:** Set up FastAPI with Supabase connectivity and the `trace_id` middleware.
2. **ETL Engine:** Build the Pandas/AI integration with the staging table pattern.
3. **HITL Workflow:** Implement the "Pause and Approve" logic between Staging and Production.
4. **Frontend Modules:** Build the two distinct modules (ABASE and GDS) with their respective themes.
5. **Final Integration:** Connect the Frontend "Execute" button to the Backend API and wire up the audit logs.