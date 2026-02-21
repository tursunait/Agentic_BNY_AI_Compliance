"""Ingest a regulation JSON document into the Weaviate Regulations collection.

Usage:
  python scripts/ingest_regulation_json.py \
    --input knowledge_base/regulations/sar_filing_instructions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config.settings import settings
from backend.knowledge_base.weaviate_client import WeaviateClient


def _as_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _build_chunks(document: Dict[str, Any]) -> List[Dict[str, str]]:
    title = str(document.get("document_title", "Regulatory Guidance")).strip()
    authority = str(document.get("issuing_authority", "Unknown")).strip()
    source_url = str(document.get("source_url", "")).strip()
    default_effective_date = str(document.get("effective_date", "")).strip()
    report_types = ", ".join(_as_text_list(document.get("report_types")))

    sections = document.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    chunks: List[Dict[str, str]] = []
    if not sections:
        text = str(document.get("text", "")).strip()
        if text:
            chunks.append(
                {
                    "text": text,
                    "regulation_name": f"{authority} - {title}".strip(" -"),
                    "section": str(document.get("document_id", "full-document")),
                    "effective_date": default_effective_date,
                    "source_url": source_url,
                }
            )
        return chunks

    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or section.get("section_number") or "unknown").strip()
        section_title = str(section.get("section_title", "")).strip()
        section_text = str(section.get("text", "")).strip()
        if not section_text:
            continue

        keywords = ", ".join(_as_text_list(section.get("keywords")))
        practical = str(section.get("practical_guidance", "")).strip()
        usage_notes = str(section.get("agent_usage_notes", "")).strip()
        section_effective_date = str(section.get("effective_date", "")).strip() or default_effective_date

        parts = []
        if section_title:
            parts.append(f"Section Title: {section_title}")
        if section.get("section_number"):
            parts.append(f"Section Number: {section.get('section_number')}")
        if report_types:
            parts.append(f"Report Types: {report_types}")
        if keywords:
            parts.append(f"Keywords: {keywords}")
        parts.append(section_text)
        if practical:
            parts.append(f"Practical Guidance: {practical}")
        if usage_notes:
            parts.append(f"Agent Usage Notes: {usage_notes}")

        chunks.append(
            {
                "text": "\n\n".join(parts),
                "regulation_name": f"{authority} - {title}".strip(" -"),
                "section": section_id,
                "effective_date": section_effective_date,
                "source_url": source_url,
            }
        )
    return chunks


def _chunk_exists(client: WeaviateClient, chunk: Dict[str, str]) -> bool:
    operands = [
        {
            "path": ["regulation_name"],
            "operator": "Equal",
            "valueText": chunk.get("regulation_name", ""),
        },
        {
            "path": ["section"],
            "operator": "Equal",
            "valueText": chunk.get("section", ""),
        },
    ]
    source_url = chunk.get("source_url", "")
    if source_url:
        operands.append(
            {
                "path": ["source_url"],
                "operator": "Equal",
                "valueText": source_url,
            }
        )

    response = (
        client.client.query.get("Regulations", ["regulation_name", "section"])
        .with_where({"operator": "And", "operands": operands})
        .with_limit(1)
        .do()
    )
    rows = response.get("data", {}).get("Get", {}).get("Regulations", [])
    return len(rows) > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a regulation JSON file into Weaviate")
    parser.add_argument(
        "--input",
        default="knowledge_base/regulations/sar_filing_instructions.json",
        help="Path to regulation JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print chunk count without writing to Weaviate",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Insert all chunks even if same regulation_name+section already exists",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}")
        return 1

    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: invalid JSON in {input_path}: {exc}")
        return 1

    if not isinstance(document, dict):
        print("ERROR: expected top-level JSON object")
        return 1

    chunks = _build_chunks(document)
    if not chunks:
        print("ERROR: no regulation chunks generated from input")
        return 1

    print(f"Prepared {len(chunks)} regulation chunks from {input_path}")
    if args.dry_run:
        print("Dry run enabled. Nothing was written to Weaviate.")
        return 0

    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY is required for embeddings.")
        return 1

    try:
        client = WeaviateClient(settings.WEAVIATE_URL, settings.WEAVIATE_API_KEY)
        client.create_schema()
        created_ids = []
        skipped = 0
        for idx, chunk in enumerate(chunks, start=1):
            if not args.force and _chunk_exists(client, chunk):
                skipped += 1
                print(f"[{idx}/{len(chunks)}] skipped duplicate section={chunk['section']}")
                continue
            object_id = client.add_regulation(chunk)
            created_ids.append(object_id)
            print(f"[{idx}/{len(chunks)}] added section={chunk['section']} id={object_id}")
    except Exception as exc:
        print(f"ERROR: failed to ingest chunks into Weaviate: {exc}")
        return 1

    print(f"Done. Inserted {len(created_ids)} regulation chunks. Skipped {skipped} duplicates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
