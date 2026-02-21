"""Test the complete CrewAI workflow with sample transaction data."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.orchestration.crew import create_compliance_crew

sample_transaction = {
    "customer_id": "CUST-12345",
    "customer_name": "Jane Smith",
    "account_number": "XXXX5678",
    "transactions": [
        {"date": "2024-01-15", "type": "cash_deposit", "amount": 9200, "branch": "Downtown", "branch_id": "BR-001"},
        {"date": "2024-01-16", "type": "cash_deposit", "amount": 9500, "branch": "Uptown", "branch_id": "BR-002"},
        {"date": "2024-01-17", "type": "cash_deposit", "amount": 9800, "branch": "Midtown", "branch_id": "BR-003"},
        {"date": "2024-01-18", "type": "cash_deposit", "amount": 9100, "branch": "Westside", "branch_id": "BR-004"},
    ],
    "total_amount": 37600,
    "customer_info": {"ssn": "XXX-XX-1234", "dob": "1985-03-15", "address": "123 Main St, Anytown, USA"},
}


def _load_input(path: str | None) -> dict:
    if not path:
        return sample_transaction
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="",
        help="Optional path to case JSON file. If omitted, built-in sample is used.",
    )
    args = parser.parse_args()
    payload = _load_input(args.input or None)

    print("=" * 60)
    print("TESTING COMPLIANCE CREW WORKFLOW")
    print("=" * 60)
    print("\nInput Transaction Data:")
    print(json.dumps(payload, indent=2))
    print("\n" + "=" * 60)
    print("STARTING CREW EXECUTION...")
    print("=" * 60 + "\n")
    result = create_compliance_crew(payload)
    print("\n" + "=" * 60)
    print("FINAL RESULT:")
    print("=" * 60)
    print(json.dumps(result, indent=2))
    if result.get("final", {}).get("pdf_path"):
        print(f"\n✅ SUCCESS! PDF generated at: {result['final']['pdf_path']}")
    else:
        print(f"\n⚠️  Report needs review: {result['final'].get('message')}")


if __name__ == "__main__":
    main()
