import uuid
from datetime import datetime

from pydantic import BaseModel

from app.profiles.models import DocumentFormat, ParseStatus


class ResumeOut(BaseModel):
    id: uuid.UUID
    file_name: str
    file_format: DocumentFormat
    file_size_bytes: int
    parse_status: ParseStatus
    parse_error: str | None
    parse_warnings: list[str]
    parser_name: str | None
    is_primary: bool
    pending_suggestions: int
    total_suggestions: int
    created_at: datetime


class ResumeDetail(ResumeOut):
    parsed_text: str | None  # the raw extracted text, shown so the user can compare
