import re
from typing import List, Dict, Any

def categorize_rule(rule_id: str) -> str:
    """
    Determine the category (completeness, compliance, accuracy, narrative)
    based on rule_id keywords.
    """
    rid = rule_id.upper()
    if "NARRATIVE" in rid:
        return "narrative"
    if any(x in rid for x in ["TIN", "NAME", "ADDRESS", "CITY", "STATE", "ZIP", "COUNTRY", "DOB", "IDENTIFICATION"]):
        return "completeness"
    if any(x in rid for x in ["AMOUNT", "FORMAT", "CONSISTENCY", "THRESHOLD", "TIMING"]):
        return "accuracy"
    if any(x in rid for x in ["REGULATOR", "OFAC", "STRUCTURING", "REDFLAG", "COMPLIANCE"]):
        return "compliance"
    # default
    return "completeness"

def calculate_score(violations: List[Dict], all_rules: List[Dict], report_type: str) -> Dict:
    """
    Calculate per‑category scores and overall validation score based on violations.
    Returns dict with total_score, status, pass_or_not, and category scores.
    """
    categories = ["completeness", "compliance", "accuracy", "narrative"]
    category_possible = {cat: 0 for cat in categories}
    category_earned = {cat: 0 for cat in categories}
    critical_failed = False

    # Severity weights
    severity_weight = {
        "critical": 5,
        "high": 3,
        "medium": 1,
        "low": 0.5
    }

    # First, compute possible weights for each rule in all_rules
    for rule in all_rules:
        rule_id = rule["rule_id"]
        severity = rule["severity"]
        weight = severity_weight.get(severity, 1)

        cat = categorize_rule(rule_id)
        category_possible[cat] += weight

        # Check if this rule is violated
        violated = any(v["rule_id"] == rule_id for v in violations)
        if not violated:
            category_earned[cat] += weight
        else:
            if severity == "critical":
                critical_failed = True

    # Add LLM-generated violations (e.g., SAR-QUALITY-001) – they are not in all_rules,
    # so they won't affect possible weight, but they will affect narrative score via override.
    # We'll handle narrative score separately later.

    # Compute category scores (percentage)
    category_scores = {}
    for cat in categories:
        if category_possible[cat] > 0:
            cat_score = (category_earned[cat] / category_possible[cat]) * 100
        else:
            cat_score = 100.0  # no rules in this category -> assume perfect
        category_scores[cat] = round(cat_score, 2)

    # For CTR, narrative category is irrelevant; set to 100
    if report_type == "CTR":
        category_scores["narrative"] = 100.0

    # Overall validation score: average of category scores
    total_score = sum(category_scores.values()) / len(category_scores)

    # Determine status
    if critical_failed:
        status = "REJECTED"
        pass_or_not = "No"
    elif total_score >= 80:
        status = "APPROVED"
        pass_or_not = "Yes"
    else:
        status = "NEEDS_REVIEW"
        pass_or_not = "No"

    return {
        "validation_score": round(total_score, 2),
        "status": status,
        "pass_or_not": pass_or_not,
        "scores": category_scores
    }