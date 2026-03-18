import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

def call_llm(prompt: str, max_retries: int = 3) -> dict:
    """Call ZhiPu GLM-5 API with retry and return parsed JSON."""
    api_key = os.getenv("DEFAULT_LLM_API_KEY")
    base_url = os.getenv("DEFAULT_LLM_BASE_URL")
    model = os.getenv("DEFAULT_LLM_MODEL_NAME", "glm-5")
    timeout = int(os.getenv("DEFAULT_LLM_TIMEOUT", 300))

    if not api_key:
        raise ValueError("LLM API key not found in environment")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout
            )
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"Timeout on attempt {attempt+1}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"LLM API call timed out after {max_retries} attempts")
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"Error on attempt {attempt+1}: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"LLM API call failed: {str(e)}")

def validate_with_llm(report: dict, rules: list, legal_requirements: list) -> dict:
    """
    Send report, rules, and legal requirements to LLM and get validation result.
    Expected LLM output JSON with fields:
        completeness_score (float 0-100)
        compliance_score (float 0-100)
        accuracy_score (float 0-100)
        narrative_score (float 0-100)
        status (str, either "APPROVED" or "NEEDS_REVIEW")
        validation_report (str, detailed text report)
    """
    prompt = f"""You are a BSA/AML compliance expert. Validate the following financial report against the provided rules and legal requirements.

**Instructions**:
1. Analyze the report carefully.
2. For each rule, determine if the report violates it.
3. Based on the violations, assign scores (0-100) for four categories:
   - completeness: presence of all required fields (names, addresses, IDs, etc.)
   - compliance: adherence to BSA, AML, OFAC, anti‑structuring rules
   - accuracy: correct data formats, amounts, dates, consistency
   - narrative: quality of SAR narrative (for CTR, always 100 if not applicable)
4. Provide a detailed textual validation report summarizing findings and recommendations.
5. Determine overall status:
   - If the average of the four scores is ≥ 80, status = "APPROVED".
   - Otherwise, status = "NEEDS_REVIEW".
6. Output ONLY a valid JSON object with exactly these keys:
   {{
       "completeness_score": float,
       "compliance_score": float,
       "accuracy_score": float,
       "narrative_score": float,
       "status": "APPROVED" or "NEEDS_REVIEW",
       "validation_report": "detailed multi-line string"
   }}

**Report**:
{json.dumps(report, indent=2)}

**Validation Rules**:
{json.dumps(rules, indent=2)}

**Legal Requirements (for context)**:
{json.dumps(legal_requirements, indent=2)}
"""
    return call_llm(prompt)
