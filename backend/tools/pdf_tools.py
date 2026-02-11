import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict

from crewai.tools import tool
from loguru import logger
from pdfrw import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


@tool("Fill PDF Form")
def fill_pdf_form_tool(template_path: str, field_data: str, output_path: str) -> str:
    try:
        fields = json.loads(field_data)
        template = PdfReader(template_path)
        for page in template.pages:
            annots = getattr(page, "Annots", None)
            if not annots:
                continue
            for annot in annots:
                if getattr(annot, "Subtype", None) != "/Widget":
                    continue
                raw_name = getattr(annot, "T", None)
                if not raw_name:
                    continue
                try:
                    name = raw_name.to_unicode().strip("()")
                except Exception:
                    name = str(raw_name)
                if name not in fields:
                    continue
                value = str(fields[name])
                annot.V = value
                annot.AP = annot.V
        writer = PdfWriter()
        for page in template.pages:
            writer.addpage(page)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        writer.write(output_path)
        return json.dumps({"status": "ok", "path": output_path})
    except Exception as exc:
        logger.error("Fill PDF form failed: %s", exc)
        return json.dumps({"error": str(exc)})


@tool("Generate Report PDF")
def generate_report_pdf_tool(case_data: str, narrative: str, report_type: str) -> str:
    try:
        data = json.loads(case_data)
    except json.JSONDecodeError:
        data = {"raw": case_data}

    report_id = uuid.uuid4().hex
    os.makedirs("/tmp/reports", exist_ok=True)
    path = f"/tmp/reports/{report_id}.pdf"
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    c.setTitle(f"{report_type} Report {report_id}")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, f"{report_type} Compliance Report")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 90, f"Report ID: {report_id}")
    c.drawString(72, height - 105, f"Generated: {datetime.utcnow().isoformat()}Z")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, height - 135, "Case Summary")
    c.setFont("Helvetica", 10)
    y = height - 150
    for key, value in data.items():
        if y < 100:
            c.showPage()
            y = height - 72
        c.drawString(72, y, f"{key}: {value}")
        y -= 14

    c.setFont("Helvetica-Bold", 12)
    if y < 160:
        c.showPage()
        y = height - 72
    c.drawString(72, y, "Narrative")
    y -= 18
    c.setFont("Helvetica", 10)
    for line in narrative.split("\n"):
        if y < 72:
            c.showPage()
            y = height - 72
        c.drawString(72, y, line)
        y -= 14

    c.showPage()
    c.save()

    metadata = {
        "report_id": report_id,
        "report_type": report_type,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    return json.dumps({"status": "ok", "path": path, "metadata": metadata})
