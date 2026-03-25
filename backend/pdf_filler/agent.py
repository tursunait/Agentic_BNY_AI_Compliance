"""
PDF Filler Agent — Supabase metadata + AcroForm fill for compliance reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from backend.pdf_filler.fill_engine import default_output_path, run_fill_pipeline
from backend.pdf_filler.metadata import fetch_report_type_row, normalize_report_type_code
from backend.pdf_filler.sar_flatten import maybe_flatten_sar_transaction_json


@dataclass
class PdfFillerResult:
    status: str
    pdf_path: str | None = None
    report_type_code: str | None = None
    fields_filled: int = 0
    error: str | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status}
        if self.pdf_path:
            d["pdf_path"] = self.pdf_path
        if self.report_type_code:
            d["report_type_code"] = self.report_type_code
        if self.fields_filled:
            d["fields_filled"] = self.fields_filled
        if self.error:
            d["error"] = self.error
        if self.warnings:
            d["warnings"] = self.warnings
        return d


class PdfFillerAgent:
    """
    Input shape:
        {
            "report_type_name": "SANCTIONS_REJECT",
            "transaction_json": { ... },  # root object for dot-path mapping (institution.*, transaction.*, …)
            "narrative_text": "optional"
        }
    """

    def __init__(self, outputs_dir: Path | None = None) -> None:
        self.outputs_dir = outputs_dir

    def fill_report(
        self,
        report_type_name: str,
        transaction_json: dict[str, Any],
        narrative_text: str | None = None,
        output_path: Path | str | None = None,
    ) -> PdfFillerResult:
        if not isinstance(transaction_json, dict):
            return PdfFillerResult(status="error", error="transaction_json must be an object")

        try:
            row = fetch_report_type_row(report_type_name)
        except LookupError as e:
            logger.error("{}", e)
            return PdfFillerResult(status="error", error=str(e))
        except Exception as e:
            logger.exception("Supabase report_types fetch failed")
            return PdfFillerResult(status="error", error=str(e))

        code = row.get("report_type_code") or normalize_report_type_code(report_type_name)
        mapping = row.get("pdf_field_mapping") or {}
        url = row["pdf_template_path"]
        out = Path(output_path) if output_path else default_output_path(code, self.outputs_dir)

        tj = transaction_json
        sparse_fill = False
        if str(code).upper() == "SAR":
            # Nested case JSON → FinCEN flat keys; sparse fill avoids 100+ “missing key” warnings
            tj = maybe_flatten_sar_transaction_json(dict(transaction_json))
            sparse_fill = True

        try:
            result = run_fill_pipeline(
                pdf_template_url=url,
                pdf_field_mapping=mapping,
                transaction_json=tj,
                narrative_text=narrative_text,
                report_type_code=code,
                output_path=out,
                sparse_fill=sparse_fill,
            )
        except Exception as e:
            logger.exception("PDF fill failed")
            return PdfFillerResult(status="error", report_type_code=code, error=str(e))

        return PdfFillerResult(
            status=result["status"],
            pdf_path=result["pdf_path"],
            report_type_code=result.get("report_type_code"),
            fields_filled=result.get("fields_filled", 0),
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Accept full agent payload dict."""
        name = payload.get("report_type_name") or payload.get("report_type_code")
        data = payload.get("transaction_json")
        if data is None:
            data = payload.get("case_data") or payload.get("data") or {}
        narrative = payload.get("narrative_text")
        if narrative is None:
            narrative = payload.get("narrative")
        out = payload.get("output_path")
        r = self.fill_report(
            report_type_name=str(name or ""),
            transaction_json=data if isinstance(data, dict) else {},
            narrative_text=narrative if isinstance(narrative, str) else None,
            output_path=out,
        )
        return r.to_dict()
