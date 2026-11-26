from typing import Literal
from contextlib import asynccontextmanager

import logging

import pyodbc
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from app.database import DatabaseCheckError, check_database
from app.auth import router as auth_router
from app.camera_configuration import router as camera_configuration_router
from app.inspection_reports import router as inspection_reports_router
from app.recognition import router as recognition_router
from app.snapshot_batches import router as snapshot_batches_router
from app.snapshot_analysis import AnalysisWorker, QueueError, QueueSettings
from app.snapshot_analysis_api import router as snapshot_analysis_router
from app.recorder import RecorderSettings
from app.review_schedules import ReviewScheduler
from app.review_schedule_api import router as review_schedule_router
from app.checking_rules_api import router as checking_rules_router

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DatabaseHealthResponse(HealthResponse):
    database: str


@asynccontextmanager
async def lifespan(application: FastAPI):
    worker = None
    scheduler = None
    try:
        settings = QueueSettings()
        if settings.worker_enabled:
            worker = AnalysisWorker(RecorderSettings().snapshot_dir, settings.concurrency)
            worker.start()
            scheduler = ReviewScheduler(RecorderSettings().snapshot_dir)
            scheduler.start()
    except (QueueError, ValidationError) as error:
        logger.error("AI review or schedule worker could not start (%s). Check archive/configuration.", type(error).__name__)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()
        if worker is not None:
            worker.stop()


app = FastAPI(title="CCTV Recognition API", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(camera_configuration_router)
app.include_router(inspection_reports_router)
app.include_router(recognition_router)
app.include_router(snapshot_batches_router)
app.include_router(snapshot_analysis_router)
app.include_router(review_schedule_router)
app.include_router(checking_rules_router)


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(
    "/api/health/database", response_model=DatabaseHealthResponse, tags=["health"]
)
def database_health() -> DatabaseHealthResponse:
    try:
        database = check_database()
    except (ValidationError, pyodbc.Error, DatabaseCheckError) as error:
        logger.error("Database health check failed (%s).", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Database unavailable. Check backend configuration."
        ) from None
    return DatabaseHealthResponse(status="ok", database=database)
