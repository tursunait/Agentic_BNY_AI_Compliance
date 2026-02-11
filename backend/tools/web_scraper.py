import json
import os
import uuid
from urllib.parse import quote_plus

import httpx
from crewai.tools import tool
from loguru import logger
from pdfrw import PdfReader


@tool("Scrape FinCEN Website")
def scrape_fincen_tool(search_query: str) -> str:
    """Scrape FinCEN.gov search results."""
    try:
        encoded = quote_plus(search_query)
        url = f"https://www.fincen.gov/search?search_api_fulltext={encoded}"
        response = httpx.get(url, timeout=20)
        response.raise_for_status()
        snippet = response.text[:4500]
        return json.dumps({"source": url, "preview": snippet})
    except Exception as exc:
        logger.error("FinCEN scrape failed: %s", exc)
        return json.dumps({"error": str(exc)})


@tool("Download Regulatory PDF")
def download_regulation_tool(url: str) -> str:
    """Download PDF and return metadata."""
    try:
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        os.makedirs("/tmp/regulations", exist_ok=True)
        file_id = uuid.uuid4().hex
        path = f"/tmp/regulations/{file_id}.pdf"
        with open(path, "wb") as fh:
            fh.write(response.content)
        reader = PdfReader(path)
        pages = len(reader.pages) if hasattr(reader, "pages") else 0
        return json.dumps({"path": path, "pages": pages, "source": url})
    except Exception as exc:
        logger.error("Failed to download regulation PDF: %s", exc)
        return json.dumps({"error": str(exc)})
