from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

CheckStatus = Literal["pass", "flagged", "not_assessed"]
ReviewStatus = Literal["new", "acknowledged", "false_positive"]
Region = Literal["Hong Kong", "Macau"]


class RecognitionCheck(BaseModel):
    category: Literal["signal", "timestamp", "scene"]
    status: CheckStatus
    explanation: str


class RecognitionRecord(BaseModel):
    id: UUID
    shop_code: str
    camera_name: str
    region: Region
    filename: str
    uploaded_at: datetime
    captured_at: datetime | None
    width: int
    height: int
    outcome: Literal["flagged", "needs_review"]
    review_status: ReviewStatus
    review_note: str
    reviewed_at: datetime | None
    analyzer_version: str
    checks: list[RecognitionCheck]


class RecognitionSummary(BaseModel):
    total: int
    flagged: int
    needs_review: int
    unreviewed: int


class RecognitionPage(BaseModel):
    records: list[RecognitionRecord]
    total: int
    page: int
    page_size: int
    summary: RecognitionSummary


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = Field(default="", max_length=1000)


class Baseline(BaseModel):
    record_id: UUID
    approved_at: datetime
