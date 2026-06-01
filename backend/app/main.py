import os
import uuid
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.connectors import get_abase_pool, close_abase_pool, get_gds_pool, close_gds_pool
from app.api.migration import router as migration_router
from app.api.gds       import router as gds_router
from app.api.abase     import router as abase_router
from app.api.agent     import router as agent_router
from app.api.reviewer  import router as reviewer_router
from app.api.report    import router as report_router
from app.api.critic    import router as critic_router

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = _LOG_DIR / "backend.log"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),                          # console (as before)
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),  # persistent: backend/logs/backend.log
    ],
)
logger = logging.getLogger(__name__)
logger.info("Logging to %s", _LOG_FILE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Connecting to ABASE database...")
    await get_abase_pool()
    logger.info("Connecting to GDS database...")
    await get_gds_pool()
    logger.info("Both database pools ready.")
    yield
    await close_abase_pool()
    await close_gds_pool()
    logger.info("Database pools closed.")


app = FastAPI(
    title="Migration Agent API",
    description="AI-driven ETL pipeline migrating data from ABASE (legacy) to GDS (target).",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("FRONTEND_URL", "http://localhost:3000"),
        "http://localhost:3001",
        "http://localhost:3002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(migration_router)
app.include_router(gds_router)
app.include_router(abase_router)
app.include_router(agent_router)
app.include_router(reviewer_router)
app.include_router(report_router)
app.include_router(critic_router)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    request.state.trace_id = trace_id

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    logger.info("method=%s path=%s status=%s duration_ms=%s trace_id=%s",
                request.method, request.url.path,
                response.status_code, duration_ms, trace_id)

    response.headers["X-Trace-ID"] = trace_id
    return response


@app.get("/health")
async def health(request: Request):
    return JSONResponse({"status": "ok", "trace_id": request.state.trace_id})
