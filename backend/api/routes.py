import os
import uuid
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from backend.api.schemas import ReportSubmission
from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.supabase_client import SupabaseClient
from backend.orchestration.crew import create_compliance_crew
from backend.tools.field_mapper import determine_report_types
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler

router = APIRouter()


def run_crew_workflow(job_id: str, transaction_data: dict) -> None:
    db = SupabaseClient()

    def _stage_callback(agent: str, progress: int) -> None:
        db.update_job_status(job_id, "processing", current_agent=agent, progress=progress)

    try:
        db.update_job_status(job_id, "processing", current_agent="router", progress=5)
        result = create_compliance_crew(transaction_data, on_stage=_stage_callback)
        db.update_job_status(job_id, "completed", result=result, progress=100)
    except Exception as exc:
        db.update_job_status(job_id, "failed", error=str(exc))


@router.post("/reports/submit")
async def submit_report(submission: ReportSubmission, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    db = SupabaseClient()
    db.create_job(job_id=job_id, input_data=submission.transaction_data)
    background_tasks.add_task(run_crew_workflow, job_id, submission.transaction_data)
    return {"job_id": job_id, "status": "pending", "message": "Report generation started"}


@router.get("/reports/{job_id}/status")
async def get_job_status(job_id: uuid.UUID):
    job_id_str = str(job_id)
    db = SupabaseClient()
    job = db.get_job(job_id_str)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    report_types = (
        (job.get("result") or {}).get("router", {}).get("report_types")
        or []
    )
    return {
        "job_id": job_id_str,
        "status": job["status"],
        "current_agent": job.get("current_agent"),
        "progress": job.get("progress", 0),
        "report_types": report_types,
        "result": job.get("result"),
        "error": job.get("error_message"),
    }


@router.get("/reports/{job_id}/download")
async def download_report(job_id: uuid.UUID, report_type: str | None = None):
    job_id_str = str(job_id)
    db = SupabaseClient()
    job = db.get_job(job_id_str)
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


@router.get("/cases/list")
async def list_cases(status: str | None = None, report_type: str | None = None, query: str | None = None):
    db = SupabaseClient()
    jobs = db.list_jobs(limit=200, status=status)
    cases = []
    for job in jobs:
        result = job.get("result") or {}
        router_result = result.get("router") or {}
        aggregators = result.get("aggregator_by_type") or {}
        if "SAR" in aggregators:
            aggregator = aggregators["SAR"] or {}
        elif "CTR" in aggregators:
            aggregator = aggregators["CTR"] or {}
        else:
            aggregator = result.get("aggregator") or {}
        final = result.get("final") or {}
        final_status = str(final.get("status", "")).lower() if isinstance(final, dict) else ""
        effective_status = "needs_review" if final_status == "needs_review" else job["status"]
        case_id = (
            final.get("case_id")
            or (final.get("reports") or [{}])[0].get("case_id")
            or aggregator.get("case_id")
            or job["job_id"]
        )
        types = router_result.get("report_types") or []
        report_type_str = "BOTH" if len(types) > 1 else (types[0] if types else "-")
        risk_score = float(aggregator.get("risk_score") or 0)
        if risk_score > 1:
            risk_score = risk_score / 100.0
        cases.append({
            "job_id": job["job_id"],
            "case_id": case_id,
            "subject_name": aggregator.get("customer_name", "Unknown"),
            "amount_usd": float(aggregator.get("total_amount_involved") or router_result.get("total_cash_amount") or 0),
            "status": effective_status,
            "report_types": types,
            "report_type": report_type_str,
            "risk_score": risk_score,
            "created_at": job.get("created_at"),
            "current_agent": job.get("current_agent"),
            "progress": job.get("progress", 0),
            "result": result,
        })
    if report_type and report_type != "All":
        cases = [c for c in cases if report_type in (c.get("report_types") or [])]
    if query:
        q = query.strip().lower()
        cases = [c for c in cases if q in str(c.get("case_id", "")).lower() or q in str(c.get("subject_name", "")).lower()]
    return cases


@router.get("/cases/recent")
async def get_recent_cases(limit: int = 10):
    db = SupabaseClient()
    jobs = db.list_jobs(limit=limit)
    return jobs[:limit]


@router.get("/cases/{case_id}")
async def get_case(case_id: str):
    db = SupabaseClient()
    job = db.get_job(case_id)
    if not job:
        raise HTTPException(status_code=404, detail="Case not found")
    return job


def _avg_processing_minutes(completed_jobs: list) -> float:
    """Return average processing time in minutes for completed jobs."""
    from datetime import datetime, timezone
    deltas = []
    for j in completed_jobs:
        try:
            created = datetime.fromisoformat(j["created_at"].replace("Z", "+00:00"))
            updated = datetime.fromisoformat(j["updated_at"].replace("Z", "+00:00"))
            delta_minutes = (updated - created).total_seconds() / 60
            if 0 < delta_minutes < 1440:  # ignore outliers > 24h
                deltas.append(delta_minutes)
        except Exception:
            continue
    return round(sum(deltas) / len(deltas), 1) if deltas else 0.0


@router.get("/dashboard/metrics")
async def get_dashboard_metrics():
    db = SupabaseClient()
    jobs = db.list_jobs(limit=500)
    completed = [j for j in jobs if j["status"] == "completed"]
    active = [j for j in jobs if j["status"] in {"submitted", "processing"}]
    pending_reviews = [j for j in jobs if j["status"] in {"needs_review", "failed"}]
    status_distribution = {
        "submitted": sum(1 for j in jobs if j["status"] == "submitted"),
        "processing": sum(1 for j in jobs if j["status"] == "processing"),
        "completed": len(completed),
        "failed": sum(1 for j in jobs if j["status"] == "failed"),
    }
    sar_count = sum(1 for j in jobs if "SAR" in ((j.get("result") or {}).get("router", {}).get("report_types") or []))
    ctr_count = sum(1 for j in jobs if "CTR" in ((j.get("result") or {}).get("router", {}).get("report_types") or []))
    return {
        "total_cases": len(jobs),
        "active_cases": len(active),
        "pending_reviews": len(pending_reviews),
        "reports_generated": len(completed),
        "avg_processing_minutes": _avg_processing_minutes(completed),
        "sar_count": sar_count,
        "ctr_count": ctr_count,
        "status_distribution": status_distribution,
        "agent_performance": [
            {"agent": "Router", "avg_time": "2.3s", "success_rate": 99.0, "cases_processed": len(jobs)},
            {"agent": "Aggregator", "avg_time": "5.4s", "success_rate": 98.5, "cases_processed": len(jobs)},
            {"agent": "Narrative", "avg_time": "7.2s", "success_rate": 96.8, "cases_processed": len(jobs)},
            {"agent": "Validator", "avg_time": "3.8s", "success_rate": 97.4, "cases_processed": len(jobs)},
            {"agent": "Filer", "avg_time": "6.0s", "success_rate": 98.1, "cases_processed": len(jobs)},
        ],
    }


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
