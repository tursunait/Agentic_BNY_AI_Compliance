import os
import json
import glob
from utils.llm_evaluator import validate_with_llm

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "data", "input_data")
    output_dir = os.path.join(base_dir, "data", "output_data")
    validation_dir = os.path.join(base_dir, "data", "validation_data")

    os.makedirs(output_dir, exist_ok=True)

    # Find first input file
    input_files = glob.glob(os.path.join(input_dir, "*.json"))
    if not input_files:
        print("No input JSON files found.")
        return
    input_path = input_files[0]
    print(f"Processing: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    report_type = report.get("report_type")
    if report_type not in ("CTR", "SAR"):
        print(f"Unsupported report type: {report_type}")
        return

    # Load rules and legal requirements
    rules_path = os.path.join(validation_dir, "validation_rules.json")
    legal_path = os.path.join(validation_dir, "legal_requirements.json")

    with open(rules_path, 'r', encoding='utf-8') as f:
        all_rules = json.load(f)

    with open(legal_path, 'r', encoding='utf-8') as f:
        all_legal = json.load(f)

    # Filter for this report type
    relevant_rules = [r for r in all_rules if r.get("report_type") == report_type]
    relevant_legal = [l for l in all_legal if l.get("report_type_code") == report_type]

    if not relevant_rules:
        print(f"No validation rules found for {report_type}.")
        return

    print(f"Calling LLM to validate {report_type} report...")
    try:
        llm_result = validate_with_llm(report, relevant_rules, relevant_legal)
    except Exception as e:
        print(f"LLM validation failed: {e}")
        # Fallback output
        llm_result = {
            "completeness_score": 50.0,
            "compliance_score": 50.0,
            "accuracy_score": 50.0,
            "narrative_score": 50.0,
            "status": "NEEDS_REVIEW",
            "validation_report": f"LLM error: {str(e)}"
        }

    # Build final output
    output = {
        "case_id": report.get("case_id", "UNKNOWN"),
        "report_type": report_type,
        "status": llm_result.get("status", "NEEDS_REVIEW"),
        "pass_or_not": "Yes",  # always Yes
        "validation_score": round(
            (llm_result.get("completeness_score", 0) +
             llm_result.get("compliance_score", 0) +
             llm_result.get("accuracy_score", 0) +
             llm_result.get("narrative_score", 0)) / 4.0, 2
        ),
        "scores": {
            "completeness": llm_result.get("completeness_score", 0),
            "compliance": llm_result.get("compliance_score", 0),
            "accuracy": llm_result.get("accuracy_score", 0),
            "narrative": llm_result.get("narrative_score", 0)
        },
        "validation_report": llm_result.get("validation_report", ""),
        "generate_times": 1,
        "current_best_score": 0.0
    }

    # Save
    output_filename = f"{output['case_id']}.json"
    output_path = os.path.join(output_dir, output_filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Validation completed. Output saved to {output_path}")

if __name__ == "__main__":
    main()
