#!/usr/bin/env python3
"""
Test PDF Filler Agent using JSON files under examples/.

  python scripts/test_pdf_filler_agent.py --example ofac_reject1.json --report-type SANCTIONS_REJECT
  python scripts/test_pdf_filler_agent.py --example sar1.json --report-type SAR
      # SAR: nested JSON is auto-flattened to FinCEN keys (subject, institution, SAI, narrative).

Requires SUPABASE_URL and SUPABASE_API_KEY or SUPABASE_ANON_KEY in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.pdf_filler import PdfFillerAgent  # noqa: E402


def load_example(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test PDF Filler Agent with examples/*.json")
    parser.add_argument(
        "--example",
        default="ofac_reject1.json",
        help="Filename under examples/ (default: ofac_reject1.json)",
    )
    parser.add_argument(
        "--report-type",
        default=None,
        help="report_type_name for Supabase (default: from JSON or SANCTIONS_REJECT / SAR)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output PDF path (default: outputs/output_<TYPE>_<timestamp>.pdf)",
    )
    args = parser.parse_args()

    ex_dir = ROOT / "examples"
    ex_path = ex_dir / args.example
    if not ex_path.is_file():
        print(f"Example not found: {ex_path}", file=sys.stderr)
        print(f"Available: {[p.name for p in ex_dir.glob('*.json')]}", file=sys.stderr)
        return 1

    data = load_example(ex_path)

    # Wrapped shape { report_type_name, transaction_json, narrative_text }
    if isinstance(data.get("transaction_json"), dict):
        transaction_json = data["transaction_json"]
        narrative = data.get("narrative_text")
        report_type = args.report_type or data.get("report_type_name") or data.get("report_type_code")
    else:
        transaction_json = data
        narrative = data.get("narrative_text") or data.get("narrative")
        report_type = (
            args.report_type
            or data.get("report_type_name")
            or data.get("report_type_code")
            or data.get("report_type")
        )

    if not report_type:
        if "OFAC" in (data.get("report_type_code") or "") or "sanction" in json.dumps(data).lower():
            report_type = "SANCTIONS_REJECT"
        elif (data.get("report_type") or "").upper() == "SAR":
            report_type = "SAR"
        else:
            print("Could not infer report_type; pass --report-type", file=sys.stderr)
            return 1

    print(f"Example:     {ex_path.name}")
    print(f"Report type: {report_type}")
    print(f"Rows in JSON (top-level keys): {list(transaction_json.keys())[:12]}...")

    agent = PdfFillerAgent(outputs_dir=ROOT / "outputs")
    result = agent.fill_report(
        report_type_name=str(report_type),
        transaction_json=transaction_json,
        narrative_text=narrative if isinstance(narrative, str) else None,
        output_path=args.output,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
