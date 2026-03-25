"""
Example SAR narratives and explanations of their effectiveness.
The agent references these to align style and quality with SAR best practices.
"""

# Example narratives with short effectiveness notes (for agent context)
REFERENCE_NARRATIVES = [
    {
        "summary": "Structured cash deposits followed by immediate international wire transfers to a single beneficiary.",
        "effectiveness": (
            "Effective because it clearly documents structured deposits with specific dates and amounts, "
            "links deposits to subsequent wire transfers, and explains why the activity deviates from the "
            "customer’s stated occupation. It includes the internal bank reference number, identifies the "
            "beneficiary and destination bank, states that investigation is continuing, and specifies "
            "where supporting documentation is maintained. The narrative supports the SAR filing using "
            "objective facts without concluding criminal conduct."
        ),
    },
    {
        "summary": "Corporate accounts exhibiting repeated structured cash deposits, high-volume wires, and foreign correspondent banking involvement.",
        "effectiveness": (
            "Effective because it systematically outlines suspicious patterns including structuring behavior, "
            "large and even-dollar transactions, rapid transaction frequency, and foreign wire activity through "
            "correspondent accounts. It quantifies total deposits and wire volumes, documents due diligence "
            "efforts (including outreach to foreign banks), references previously filed CTRs and SARs, "
            "and states the institution’s decision to terminate the relationships. The narrative demonstrates "
            "investigative steps taken and clearly identifies the location of supporting records."
        ),
    },
    {
        "summary": "Grocery store owner engaging in structured deposits, bulk cash activity, and repeated international wires indicative of informal value transfer operations.",
        "effectiveness": (
            "Effective because it integrates personal and business account activity to demonstrate aggregate "
            "structuring patterns, provides specific transaction counts and ranges, and documents how deposits "
            "were coordinated across accounts to exceed CTR thresholds. It connects transaction behavior to "
            "licensing concerns and OFAC exposure, references due diligence conducted with regulatory authorities, "
            "and clearly states additional actions taken including law enforcement contact and account closure. "
            "The narrative presents source and use of funds analysis while maintaining a factual, neutral tone."
        ),
    },
]

EFFECTIVENESS_GUIDELINES = """
SAR narrative effectiveness guidelines (use when generating):

1. Use only information explicitly provided in the input. Do not add names, dates, amounts, or facts not present.
2. Maintain a factual and objective tone. Do not conclude that a crime occurred; describe observed activity and patterns.
3. Include specific transactional detail: dates, amounts, transaction counts, account identifiers (if provided), and total aggregates where applicable.
4. Clearly connect suspicious patterns to the underlying data (e.g., structuring, rapid movement of funds, foreign wires, correspondent banking activity).
5. Where applicable, document institutional actions taken (e.g., CTR filing, account closure, law enforcement contact, ongoing investigation).
6. Identify deviations from expected customer behavior or stated business purpose when supported by the input.
7. Organize logically: subject identification → time frame → transactional details → suspicious patterns → institutional response.
8. Avoid emotional or accusatory language. Use neutral phrasing such as "appears inconsistent," "raises concern," or "may indicate."
9. Output a single continuous narrative paragraph suitable for the SAR narrative section.
10. Return the narrative in JSON format as: {"narrative": "..."}.
"""


def get_reference_context() -> str:
    """Build the reference context string for the agent."""
    parts = [
        EFFECTIVENESS_GUIDELINES,
        "\nReference examples (what makes narratives effective):",
    ]
    for i, ref in enumerate(REFERENCE_NARRATIVES, 1):
        parts.append(f"  {i}. {ref['summary']}: {ref['effectiveness']}")
    return "\n".join(parts)
