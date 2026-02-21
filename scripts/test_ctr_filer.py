"""Standalone test for CTR PDF filing."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tools.field_mapper import calculate_total_cash_amount, determine_report_types
from backend.tools.pdf_tools import CTRReportFiler


def main() -> int:
    parser = argparse.ArgumentParser(description="Test CTR PDF filer")
    parser.add_argument(
        "--input",
        default="data/CASE-2024-311995__CAT-29-31-33-35.json",
        help="Path to case JSON file",
    )
    parser.add_argument(
        "--template",
        default="knowledge_base/documents/pdf_templates/ctr_report.pdf",
        help="Path to CTR PDF template",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CTR PDF FILER TEST")
    print("=" * 60)
    print(f"\nInput:    {args.input}")
    print(f"Template: {args.template}")

    try:
        with open(args.input, "r", encoding="utf-8") as handle:
            case_data = json.load(handle)
    except Exception as exc:
        print(json.dumps({"status": "error", "error": f"Unable to read input: {exc}"}, indent=2))
        return 1

    report_types = determine_report_types(case_data)
    total_cash = calculate_total_cash_amount(case_data)
    print(f"Detected report types: {report_types}")
    print(f"Total cash amount: ${total_cash:,.2f}")

    if "CTR" not in report_types:
        print("\nNo CTR filing requirement detected for this case.")
        return 0

    filer = CTRReportFiler(template_path=args.template)
    result = filer.fill_from_dict(case_data)
    print(json.dumps(result, indent=2))

    if result.get("status") == "success":
        print(f"\nSUCCESS: CTR PDF saved to {result['pdf_path']}")
        print(f"Fields filled: {result['fields_filled']}")
        if result.get("fill_errors"):
            print(f"Warnings ({len(result['fill_errors'])}):")
            for warning in result["fill_errors"]:
                print(f"  - {warning}")
        return 0

    print(f"\nFAILED: {result.get('error', 'Unknown error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
