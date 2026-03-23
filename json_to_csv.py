"""
Convert legal_requirements.json and validation_rules.json to CSV format.
JSON files are read from data/validation_data/ and CSV files are written to the same directory.
"""

import json
import csv
import os
from typing import List, Dict, Any

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data', 'validation_data')

LEGAL_JSON = os.path.join(DATA_DIR, 'legal_requirements.json')
VALIDATION_JSON = os.path.join(DATA_DIR, 'validation_rules.json')

LEGAL_CSV = os.path.join(DATA_DIR, 'legal_requirements.csv')
VALIDATION_CSV = os.path.join(DATA_DIR, 'validation_rules.csv')


def json_to_csv_legal():
    """Convert legal_requirements.json to CSV, removing the 'id' column."""
    try:
        with open(LEGAL_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {LEGAL_JSON}: {e}")
        return

    if not isinstance(data, list):
        print(f"Error: {LEGAL_JSON} should contain a list of objects.")
        return

    # Fields to keep (excluding 'id')
    fieldnames = ['report_type_code', 'rule_code', 'rule_description',
                  'rule_type', 'threshold_amount', 'time_limit_days', 'is_mandatory']

    try:
        with open(LEGAL_CSV, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for item in data:
                # Create a new dict without the 'id' field
                row = {k: item.get(k, '') for k in fieldnames}
                # Handle None values (convert to empty string for CSV)
                for key, value in row.items():
                    if value is None:
                        row[key] = ''
                writer.writerow(row)
        print(f"Successfully converted {LEGAL_JSON} to {LEGAL_CSV}")
    except Exception as e:
        print(f"Error writing {LEGAL_CSV}: {e}")


def json_to_csv_validation():
    """Convert validation_rules.json to CSV, flattening rule_json and legal_refs."""
    try:
        with open(VALIDATION_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {VALIDATION_JSON}: {e}")
        return

    if not isinstance(data, list):
        print(f"Error: {VALIDATION_JSON} should contain a list of objects.")
        return

    # Define output columns
    fieldnames = ['rule_id', 'report_type', 'severity',
                  'condition', 'message', 'legal_refs']

    try:
        with open(VALIDATION_CSV, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for item in data:
                row = {}
                # Top-level fields
                row['rule_id'] = item.get('rule_id', '')
                row['report_type'] = item.get('report_type', '')
                row['severity'] = item.get('severity', '')

                # Extract from rule_json
                rule_json = item.get('rule_json', {})
                row['condition'] = rule_json.get('condition', '')
                row['message'] = rule_json.get('message', '')

                # Convert legal_refs list to comma-separated string
                legal_refs = item.get('legal_refs', [])
                if legal_refs and isinstance(legal_refs, list):
                    row['legal_refs'] = ', '.join(legal_refs)
                else:
                    row['legal_refs'] = ''

                writer.writerow(row)
        print(f"Successfully converted {VALIDATION_JSON} to {VALIDATION_CSV}")
    except Exception as e:
        print(f"Error writing {VALIDATION_CSV}: {e}")


def main():
    print("Starting JSON to CSV conversion...")
    json_to_csv_legal()
    json_to_csv_validation()
    print("Done.")


if __name__ == "__main__":
    main()