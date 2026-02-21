"""Standalone compliance PDF filer test (SAR/CTR/BOTH)."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.field_mapper import determine_report_types
from backend.tools.pdf_tools import CTRReportFiler, SARReportFiler


def _run_filer(case_data: dict, report_type: str, sar_template: str | None, ctr_template: str | None) -> dict:
    if report_type == "CTR":
        filer = CTRReportFiler(template_path=ctr_template) if ctr_template else CTRReportFiler()
    else:
        filer = SARReportFiler(template_path=sar_template) if sar_template else SARReportFiler()
    return filer.fill_from_dict(case_data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test compliance PDF filing")
    parser.add_argument(
        "--input",
        default="data/CASE-2024-311995__CAT-29-31-33-35.json",
        help="Path to case JSON input file",
    )
    parser.add_argument(
        "--report-type",
        default="auto",
        choices=["auto", "SAR", "CTR", "BOTH"],
        help="Select report type or auto-detect from case data",
    )
    parser.add_argument(
        "--sar-template",
        default="knowledge_base/documents/pdf_templates/sar_report.pdf",
        help="Path to fillable SAR template PDF",
    )
    parser.add_argument(
        "--ctr-template",
        default="knowledge_base/documents/pdf_templates/ctr_report.pdf",
        help="Path to fillable CTR template PDF",
    )
    args = parser.parse_args()

    print(f"\nInput:        {args.input}")
    print(f"Report Type:  {args.report_type}")
    print(f"SAR Template: {args.sar_template}")
    print(f"CTR Template: {args.ctr_template}\n")

    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            case_data = json.load(handle)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"Unable to load input: {exc}"}, indent=2))
        return 1

    requested = args.report_type.upper()
    if requested == "AUTO":
        report_types = determine_report_types(case_data)
    elif requested == "BOTH":
        report_types = ["CTR", "SAR"]
    else:
        report_types = [requested]

    if not report_types:
        print(json.dumps({"status": "no_filing_required", "message": "No CTR or SAR requirement detected"}, indent=2))
        return 0

    results = []
    for report_type in report_types:
        print(f"Filing {report_type}...")
        result = _run_filer(case_data, report_type, args.sar_template, args.ctr_template)
        results.append(result)
        print(json.dumps(result, indent=2))
        if result.get("status") == "success":
            print(f"SUCCESS: {report_type} PDF saved to {result['pdf_path']}\n")
        else:
            print(f"FAILED: {report_type} => {result.get('error', 'Unknown error')}\n")

    if len(results) == 1:
        return 0 if results[0].get("status") == "success" else 1
    all_ok = all(item.get("status") == "success" for item in results)
    print(json.dumps({"status": "success" if all_ok else "error", "reports": results}, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
