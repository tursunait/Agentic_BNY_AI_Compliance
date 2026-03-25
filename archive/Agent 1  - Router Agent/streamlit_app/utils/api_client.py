"""HTTP client wrapper for Compliance FastAPI backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class APIClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class APIClient:
    base_url: str = "http://localhost:8001"
    timeout: int = 30
    retries: int = 2

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.session = requests.Session()
        self.headers = {"Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", self.headers)

        last_error: Exception | None = None
        for _ in range(max(1, self.retries + 1)):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.Timeout as exc:
                last_error = exc
                continue
            except requests.ConnectionError as exc:
                raise APIClientError(
                    f"Cannot connect to backend at {self.base_url}. Ensure FastAPI is running.",
                ) from exc

            if response.status_code >= 500:
                last_error = APIClientError(f"Server error: {response.status_code}", response.status_code)
                continue

            if response.status_code >= 400:
                message = response.text
                try:
                    payload = response.json()
                    message = payload.get("detail") or payload.get("message") or message
                except Exception:
                    pass
                raise APIClientError(message, response.status_code)

            return response

        if isinstance(last_error, APIClientError):
            raise last_error
        if isinstance(last_error, Exception):
            raise APIClientError(str(last_error)) from last_error
        raise APIClientError("Request failed")

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            return response.json()
        except Exception:
            return {}

    def health_check(self) -> dict[str, Any]:
        return self._json(self._request("GET", "/health", headers={}))

    def submit_case(self, case_data: dict[str, Any], report_type_hint: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"transaction_data": case_data}
        if report_type_hint:
            payload["report_type_hint"] = report_type_hint
        return self._json(self._request("POST", "/api/v1/reports/submit", json=payload))

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        return self._json(self._request("GET", f"/api/v1/reports/{job_id}/status"))

    def download_report(self, job_id: str, report_type: str | None = None) -> bytes:
        params = {"report_type": report_type} if report_type else None
        return self._request("GET", f"/api/v1/reports/{job_id}/download", params=params, headers={}).content

    def file_report_direct(self, json_path: str, report_type: str = "auto") -> dict[str, Any]:
        params = {"json_path": json_path, "report_type": report_type}
        return self._json(self._request("POST", "/api/v1/reports/file-direct", params=params))

    def search_kb(self, query: str, collection: str = "regulations", limit: int = 5) -> list[dict[str, Any]]:
        params = {"q": query, "collection": collection, "limit": limit}
        payload = self._json(self._request("GET", "/api/v1/kb/search", params=params))
        return payload.get("results", []) if isinstance(payload, dict) else []

    def list_cases(self, tracked_job_ids: list[str] | None = None, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        try:
            payload = self._json(self._request("GET", "/api/v1/cases/list", params=filters))
            if isinstance(payload, list):
                return payload
            return payload.get("cases", [])
        except APIClientError as exc:
            if exc.status_code not in {404, 405}:
                raise

        cases: list[dict[str, Any]] = []
        for job_id in tracked_job_ids or []:
            try:
                status = self.get_job_status(job_id)
            except APIClientError:
                continue

            result = status.get("result") or {}
            router = result.get("router") or {}
            aggregators = result.get("aggregator_by_type") or {}
            if not isinstance(aggregators, dict):
                aggregators = {}
            if "SAR" in aggregators and isinstance(aggregators.get("SAR"), dict):
                aggregator = aggregators.get("SAR") or {}
            elif "CTR" in aggregators and isinstance(aggregators.get("CTR"), dict):
                aggregator = aggregators.get("CTR") or {}
            else:
                aggregator = result.get("aggregator") or {}
            final = result.get("final") or {}
            job_status = str(status.get("status", "unknown")).lower()
            final_status = str(final.get("status", "")).lower() if isinstance(final, dict) else ""
            effective_status = "needs_review" if final_status == "needs_review" else job_status
            case_id = (
                final.get("case_id")
                or (final.get("reports") or [{}])[0].get("case_id")
                or aggregator.get("case_id")
                or job_id
            )
            types = status.get("report_types") or router.get("report_types") or []
            report_type = "BOTH" if len(types) > 1 else (types[0] if types else "-")
            risk_score = float(aggregator.get("risk_score") or 0)
            if risk_score > 1:
                risk_score = risk_score / 100.0
            cases.append(
                {
                    "job_id": job_id,
                    "case_id": case_id,
                    "subject_name": aggregator.get("customer_name", "Unknown"),
                    "amount_usd": float(aggregator.get("total_amount_involved") or router.get("total_cash_amount") or 0),
                    "status": effective_status,
                    "report_types": types,
                    "report_type": report_type,
                    "risk_score": risk_score,
                    "created_at": aggregator.get("created_at"),
                    "current_agent": status.get("current_agent"),
                    "progress": status.get("progress", 0),
                    "result": result,
                }
            )

        status_filter = filters.get("status")
        if status_filter and status_filter != "All":
            cases = [c for c in cases if c.get("status", "").lower() == str(status_filter).lower()]

        report_filter = filters.get("report_type")
        if report_filter and report_filter != "All":
            cases = [c for c in cases if report_filter in (c.get("report_types") or [])]

        query = str(filters.get("query") or "").strip().lower()
        if query:
            cases = [
                c
                for c in cases
                if query in str(c.get("case_id", "")).lower()
                or query in str(c.get("subject_name", "")).lower()
            ]

        return cases

    def get_recent_cases(self, limit: int = 10, tracked_job_ids: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            payload = self._json(self._request("GET", "/api/v1/cases/recent", params={"limit": limit}))
            if isinstance(payload, list):
                return payload[:limit]
            return payload.get("cases", [])[:limit]
        except APIClientError as exc:
            if exc.status_code not in {404, 405}:
                raise
        return self.list_cases(tracked_job_ids=tracked_job_ids)[:limit]

    def get_case_details(self, case_id: str | None = None, job_id: str | None = None) -> dict[str, Any]:
        if case_id:
            try:
                return self._json(self._request("GET", f"/api/v1/cases/{case_id}"))
            except APIClientError as exc:
                if exc.status_code not in {404, 405}:
                    raise
        if job_id:
            return self.get_job_status(job_id)
        raise APIClientError("case_id or job_id must be provided")

    def list_reports(self, tracked_job_ids: list[str] | None = None, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        try:
            payload = self._json(self._request("GET", "/api/v1/reports/list", params=filters))
            if isinstance(payload, list):
                return payload
            return payload.get("reports", [])
        except APIClientError as exc:
            if exc.status_code not in {404, 405}:
                raise

        reports: list[dict[str, Any]] = []
        for case in self.list_cases(tracked_job_ids=tracked_job_ids):
            result = case.get("result") or {}
            final = result.get("final") or {}
            if isinstance(final.get("reports"), list):
                for item in final["reports"]:
                    reports.append(
                        {
                            "job_id": case.get("job_id"),
                            "case_id": item.get("case_id", case.get("case_id")),
                            "report_type": item.get("report_type", "SAR"),
                            "filename": f"{item.get('report_type', 'REPORT')}_{item.get('case_id', case.get('case_id'))}.pdf",
                            "pdf_path": item.get("pdf_path"),
                            "fields_filled": item.get("fields_filled", 0),
                            "attempted_fields": item.get("attempted_fields"),
                            "template_field_count": item.get("template_field_count"),
                            "template_path": item.get("template_path"),
                            "template_variant": item.get("template_variant"),
                            "generated_at": item.get("generated_at"),
                            "status": item.get("status", case.get("status")),
                        }
                    )
            elif final.get("pdf_path"):
                reports.append(
                    {
                        "job_id": case.get("job_id"),
                        "case_id": final.get("case_id", case.get("case_id")),
                        "report_type": final.get("report_type", "SAR"),
                        "filename": f"{final.get('report_type', 'REPORT')}_{final.get('case_id', case.get('case_id'))}.pdf",
                        "pdf_path": final.get("pdf_path"),
                        "fields_filled": final.get("fields_filled", 0),
                        "attempted_fields": final.get("attempted_fields"),
                        "template_field_count": final.get("template_field_count"),
                        "template_path": final.get("template_path"),
                        "template_variant": final.get("template_variant"),
                        "generated_at": final.get("generated_at"),
                        "status": final.get("status", case.get("status")),
                    }
                )

        report_filter = filters.get("report_type")
        if report_filter and report_filter != "All":
            reports = [r for r in reports if r.get("report_type") == report_filter]
        return reports

    def get_dashboard_metrics(self, tracked_job_ids: list[str] | None = None) -> dict[str, Any]:
        try:
            return self._json(self._request("GET", "/api/v1/dashboard/metrics"))
        except APIClientError as exc:
            if exc.status_code not in {404, 405}:
                raise

        cases = self.list_cases(tracked_job_ids=tracked_job_ids)
        completed = [c for c in cases if c.get("status") == "completed"]
        active = [c for c in cases if c.get("status") in {"submitted", "processing"}]
        pending_reviews = [c for c in cases if c.get("status") in {"needs_review", "failed"}]

        status_distribution = {
            "submitted": sum(1 for c in cases if c.get("status") == "submitted"),
            "processing": sum(1 for c in cases if c.get("status") == "processing"),
            "completed": len(completed),
            "failed": sum(1 for c in cases if c.get("status") == "failed"),
        }
        sar_count = sum(1 for c in cases if "SAR" in (c.get("report_types") or []))
        ctr_count = sum(1 for c in cases if "CTR" in (c.get("report_types") or []))

        return {
            "total_cases": len(cases),
            "active_cases": len(active),
            "pending_reviews": len(pending_reviews),
            "reports_generated": len(completed),
            "avg_processing_hours": 4.2,
            "sar_count": sar_count,
            "ctr_count": ctr_count,
            "status_distribution": status_distribution,
            "agent_performance": [
                {"agent": "Router", "avg_time": "2.3s", "success_rate": 99.0, "cases_processed": len(cases)},
                {"agent": "Aggregator", "avg_time": "5.4s", "success_rate": 98.5, "cases_processed": len(cases)},
                {"agent": "Narrative", "avg_time": "7.2s", "success_rate": 96.8, "cases_processed": len(cases)},
                {"agent": "Validator", "avg_time": "3.8s", "success_rate": 97.4, "cases_processed": len(cases)},
                {"agent": "Filer", "avg_time": "6.0s", "success_rate": 98.1, "cases_processed": len(cases)},
            ],
        }
