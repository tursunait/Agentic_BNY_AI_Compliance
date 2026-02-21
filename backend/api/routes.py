import os
import uuid
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import ReportSubmission
from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.postgres_client import PostgreSQLClient
from backend.orchestration.crew import create_compliance_crew
from backend.tools.field_mapper import determine_report_types
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler

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
    report_types = (
        (job.get("result") or {}).get("router", {}).get("report_types")
        or []
    )
    return {
        "job_id": job_id,
        "status": job["status"],
        "current_agent": job.get("current_agent"),
        "progress": job.get("progress", 0),
        "report_types": report_types,
        "result": job.get("result"),
        "error": job.get("error_message"),
    }


@router.get("/reports/{job_id}/download")
async def download_report(job_id: str, report_type: str | None = None):
    db = PostgreSQLClient()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Status: {job['status']}")
    if not job.get("result"):
        raise HTTPException(status_code=400, detail="Report not ready")
    final = job.get("result", {}).get("final", {})
    pdf_path = None
    case_id = "report"
    resolved_type = "SAR"

    # BOTH scenario.
    if isinstance(final.get("reports"), list):
        wanted = (report_type or "SAR").upper()
        for item in final["reports"]:
            if (item or {}).get("report_type", "").upper() == wanted:
                pdf_path = (item or {}).get("pdf_path")
                case_id = (item or {}).get("case_id", "report")
                resolved_type = wanted
                break
        if not pdf_path:
            raise HTTPException(status_code=404, detail=f"No {wanted} report found for this job")
    else:
        # Single report.
        pdf_path = final.get("pdf_path")
        case_id = final.get("case_id", "report")
        resolved_type = final.get("report_type", report_type or "SAR")

    if not pdf_path or not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF file not found")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"{resolved_type}_{case_id}.pdf",
        headers={"Content-Disposition": f'attachment; filename="{resolved_type}_{case_id}.pdf"'},
    )


@router.post("/reports/file-direct")
async def file_report_direct(
    json_path: str = "data/CASE-2024-311995__CAT-29-31-33-35.json",
    report_type: str = "auto",
):
    """Direct filing endpoint for standalone PDF tests (SAR/CTR/BOTH/auto)."""
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            case_data = json.load(handle)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    requested = (report_type or "auto").upper()
    if requested == "AUTO":
        report_types = determine_report_types(case_data)
    elif requested == "BOTH":
        report_types = ["CTR", "SAR"]
    elif requested in {"SAR", "CTR"}:
        report_types = [requested]
    else:
        raise HTTPException(status_code=400, detail="report_type must be auto, SAR, CTR, or BOTH")

    if not report_types:
        return {"status": "no_filing_required", "message": "No CTR or SAR filing requirements met"}

    results = []
    for rtype in report_types:
        filer = CTRReportFiler() if rtype == "CTR" else SARReportFiler()
        result = filer.fill_from_dict(case_data)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
        results.append(result)

    return results[0] if len(results) == 1 else {"status": "success", "reports": results}


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
