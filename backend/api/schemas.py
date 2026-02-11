from typing import Any, Dict, Optional

from pydantic import BaseModel


class ReportSubmission(BaseModel):
    transaction_data: Dict[str, Any]
    report_type_hint: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    status: str
    current_agent: Optional[str]
    progress: int
    result: Optional[Dict[str, Any]]
    error: Optional[str]
