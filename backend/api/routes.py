import json
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from backend.api.schemas import JobStatus, ReportSubmission
from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.postgres_client import PostgreSQLClient
from backend.orchestration.crew import create_compliance_crew

router = APIRouter()
def run_crew_workflow(job_id: str, transaction_data: dict) -> None:
    db = PostgreSQLClient()
    try:
        db.update_job_status(job_id, "processing", current_agent="router", progress=5)
        result = create_compliance_crew(transaction_data)
        db.update_job_status(job_id, "completed", result=result, progress=100)
    except Exception as exc:
        db.update_job_status(job_id, "failed", error=str(exc))


@router.post("/reports/submit")
async def submit_report(submission: ReportSubmission, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    db = PostgreSQLClient()
    db.create_job(job_id=job_id, input_data=submission.transaction_data)
    background_tasks.add_task(run_crew_workflow, job_id, submission.transaction_data)
    return {"job_id": job_id, "status": "submitted", "message": "Report generation started"}


@router.get("/reports/{job_id}/status")
async def get_job_status(job_id: str):
    db = PostgreSQLClient()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        current_agent=job.get("current_agent"),
        progress=job.get("progress", 0),
        result=job.get("result"),
        error=job.get("error_message"),
    )


@router.get("/reports/{job_id}/download")
async def download_report(job_id: str):
    db = PostgreSQLClient()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed" or not job.get("result"):
        raise HTTPException(status_code=400, detail="Report not ready")
    pdf_path = job["result"]["final"].get("pdf_path")
    if not pdf_path:
        raise HTTPException(status_code=400, detail="PDF missing")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"compliance_report_{job_id}.pdf")


@router.get("/kb/search")
async def search_knowledge_base(q: str, collection: str = "narratives", limit: int = 5):
    kb = KBManager()
    if collection == "narratives":
        results = kb.find_similar_narratives(q, top_k=limit)
    elif collection == "regulations":
        results = kb.search_regulations(q, top_k=limit)
    else:
        raise HTTPException(status_code=400, detail="Invalid collection")
    return {"results": results}
