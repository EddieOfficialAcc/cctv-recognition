from typing import Literal

import logging

import pyodbc
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from app.database import DatabaseCheckError, check_database
from app.auth import router as auth_router
from app.recognition import router as recognition_router

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DatabaseHealthResponse(HealthResponse):
    database: str


app = FastAPI(title="CCTV Recognition API")
app.include_router(auth_router)
app.include_router(recognition_router)


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
