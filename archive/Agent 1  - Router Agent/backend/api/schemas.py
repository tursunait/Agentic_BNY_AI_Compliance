from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class ReportSubmission(BaseModel):
    transaction_data: Union[Dict[str, Any], List[Dict[str, Any]]]
    report_type_hint: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    status: str
    current_agent: Optional[str]
    progress: int
    result: Optional[Dict[str, Any]]
    error: Optional[str]
