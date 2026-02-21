"""Seed the knowledge base with initial data."""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.knowledge_base.kb_manager import KBManager
from backend.knowledge_base.postgres_client import PostgreSQLClient

SAR_SCHEMA_PATH = ROOT / "knowledge_base" / "schemas" / "sar_schema.json"


def load_schema(schema_path: Path) -> Dict[str, Any]:
    """Load a report schema JSON file from disk."""
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    if not isinstance(schema, dict):
        raise ValueError(f"Schema file must contain a JSON object: {schema_path}")

    return schema


def seed_postgres(db: PostgreSQLClient) -> None:
    db.create_tables()
    sar_schema = load_schema(SAR_SCHEMA_PATH)
    report_type = sar_schema.get("report_type", "SAR")
    version = sar_schema.get("version", "1.0")
    effective_date = sar_schema.get("effective_date", date.today().isoformat())

    db.add_schema(
        report_type=report_type,
        version=version,
        schema_json=sar_schema,
        effective_date=effective_date,
    )
    print(f"✓ Added {report_type} schema from {SAR_SCHEMA_PATH}")

    rules = [
        {
            "rule_id": "SAR-C001",
            "report_type": report_type,
            "severity": "critical",
            "rule_json": {
                "condition": "subject.last_name IS NOT NULL OR subject.ssn IS NOT NULL",
                "message": "Subject must have last name or SSN",
            },
        }
    ]
    for rule in rules:
        db.add_validation_rule(rule)
    print(f"✓ Added {len(rules)} validation rules")


def seed_weaviate(kb: KBManager) -> None:
    narratives = [
        {
            "text": "Customer made eleven cash deposits under $10,000 all within 10 days.",
            "summary": "Multiple cash deposits just below CTR threshold",
            "activity_type": "Structuring",
            "report_type": "SAR",
            "quality_score": 9.0,
            "word_count": 42,
            "transaction_count": 11,
            "total_amount": 98600,
            "date_added": date.today().isoformat(),
        }
    ]

    for narrative in narratives:
        narrative_id = kb.add_narrative_example(narrative)
        print(f"✓ Added narrative {narrative_id}")

    regulations = [
        {
            "text": "The BSA requires filing of a SAR within 30 days of detection.",
            "regulation_name": "BSA",
            "section": "31 CFR 1020.320",
            "effective_date": date.today().isoformat(),
            "source_url": "https://www.fincen.gov/",
        }
    ]

    for regulation in regulations:
        reg_id = kb.add_regulation(regulation)
        print(f"✓ Added regulation {reg_id}")


def main() -> None:
    print("Seeding Knowledge Base...")
    db = PostgreSQLClient()
    kb = KBManager()
    print("\n1. Seeding PostgreSQL...")
    seed_postgres(db)
    print("\n2. Seeding Weaviate...")
    seed_weaviate(kb)
    print("\n✅ Knowledge Base seeded successfully!")
    print("\nYou can now run the application with 'uvicorn backend.api.main:app'")


if __name__ == "__main__":
    main()
