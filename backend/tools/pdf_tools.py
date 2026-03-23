"""SAR/CTR PDF filing tools built on pypdf."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loguru import logger
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject

from backend.tools.field_mapper import CTRFieldMapper, SARFieldMapper, normalize_case_data

# Allow standalone usage (scripts/test_pdf_filer.py) without requiring CrewAI imports.
try:
    from crewai.tools import tool
except Exception:
    def tool(*args, **kwargs):  # type: ignore[override]
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]

        def _decorator(func):
            return func

        return _decorator


SAR_TEMPLATE_PATH = Path("data/raw_data_pdf/SAR.pdf")
SAR_TEMPLATE_FALLBACK_PATH = Path("data/raw_data_pdf/SAR.pdf")
CTR_TEMPLATE_PATH = Path("data/raw_data_pdf/CTR.pdf")
OUTPUT_DIR = Path("data/output")


class BaseReportFiler:
    """Shared PDF filing behavior for compliance report forms."""

    REPORT_TYPE = "REPORT"
    PAGE2_PREFIXES: Tuple[str, ...] = ()
    PAGE3_PREFIXES: Tuple[str, ...] = ()
    PAGE3_FIELDS: set[str] = set()

    def __init__(self, template_path: str, output_dir: str = str(OUTPUT_DIR)):
        self.template_path = Path(template_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.template_path.exists():
            raise FileNotFoundError(
                f"{self.REPORT_TYPE} PDF template not found: {self.template_path}"
            )

    def fill_from_json(self, json_path: str) -> Dict:
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                case_data = json.load(handle)
            return self.fill_from_dict(case_data)
        except FileNotFoundError:
            return {"status": "error", "error": f"File not found: {json_path}"}
        except json.JSONDecodeError as exc:
            return {"status": "error", "error": f"Invalid JSON: {exc}"}

    def fill_from_dict(self, case_data: Any, injected_narrative: str | None = None) -> Dict:
        raise NotImplementedError

    def _fill_pdf(self, field_values: Dict[str, str], out_path: Path) -> Tuple[int, List[str]]:
        reader = PdfReader(str(self.template_path))
        writer = PdfWriter()
        writer.clone_reader_document_root(reader)
        # clone_reader_document_root() already carries the page tree for fillable
        # templates. Adding pages again duplicates every page in output PDFs.
        if len(writer.pages) == 0:
            for page in reader.pages:
                writer.add_page(page)
        self._ensure_need_appearances(writer)
        page_fields = [self._collect_page_fields(page) for page in writer.pages]

        filled = 0
        errors: List[str] = []
        for field_id, value in field_values.items():
            page_idx = self._page_for_field(field_id)
            if page_idx >= len(writer.pages):
                errors.append(f"{field_id}: page index {page_idx} out of range")
                continue

            # Avoid noisy warnings by targeting the page that actually owns the field.
            if field_id not in page_fields[page_idx]:
                matches = [idx for idx, names in enumerate(page_fields) if field_id in names]
                if matches:
                    page_idx = matches[0]
                else:
                    errors.append(f"{field_id}: field not found in template")
                    continue
            try:
                writer.update_page_form_field_values(
                    writer.pages[page_idx], {field_id: str(value)}, auto_regenerate=True
                )
                filled += 1
            except Exception as exc:
                msg = f"{field_id}: {exc}"
                errors.append(msg)
                logger.debug("Field fill warning: {}", msg)

        with open(out_path, "wb") as handle:
            writer.write(handle)
        return filled, errors

    def _template_field_count(self) -> int:
        try:
            return len(PdfReader(str(self.template_path)).get_fields() or {})
        except Exception:
            return 0

    @staticmethod
    def _ensure_need_appearances(writer: PdfWriter) -> None:
        """
        Ensure viewers regenerate field appearances so filled values are visible.
        """
        try:
            root = writer._root_object
            acro_form = root.get("/AcroForm")
            if acro_form is None:
                acro_form = DictionaryObject()
                root[NameObject("/AcroForm")] = acro_form
            elif hasattr(acro_form, "get_object"):
                acro_form = acro_form.get_object()
            acro_form[NameObject("/NeedAppearances")] = BooleanObject(True)
            if "/AcroForm" not in root:
                root[NameObject("/AcroForm")] = acro_form
        except Exception as exc:
            logger.debug("Could not set NeedAppearances: {}", exc)

    @staticmethod
    def _collect_page_fields(page) -> set[str]:
        names: set[str] = set()
        annots = page.get("/Annots", []) or []
        for annot_ref in annots:
            try:
                annot = annot_ref.get_object()
            except Exception:
                continue
            field_name = annot.get("/T")
            if not field_name and annot.get("/Parent"):
                try:
                    parent = annot.get("/Parent").get_object()
                    field_name = parent.get("/T")
                except Exception:
                    field_name = None
            if field_name:
                names.add(str(field_name))
        return names

    def _page_for_field(self, field_id: str) -> int:
        if field_id in self.PAGE3_FIELDS:
            return 2
        for prefix in self.PAGE3_PREFIXES:
            if field_id.startswith(prefix):
                return 2
        for prefix in self.PAGE2_PREFIXES:
            if field_id == prefix or field_id.startswith(prefix):
                return 1
        return 0

    def _audit_log(self, result: Dict) -> None:
        try:
            from backend.knowledge_base.supabase_client import SupabaseClient

            db = SupabaseClient()
            session_id = uuid.uuid4()
            try:
                session_id = uuid.UUID(result["report_id"])
            except Exception:
                pass
            db.log_audit(
                session_id=session_id,
                action="report_filed",
                agent_name="filer",
                entity_type="pdf",
                entity_id=result.get("pdf_path"),
                details={
                    "case_id": result.get("case_id"),
                    "report_type": result.get("report_type", self.REPORT_TYPE),
                    "fields_filled": result.get("fields_filled"),
                    "status": result.get("status"),
                },
            )
        except Exception as exc:
            logger.warning("Audit log write failed (non-blocking): {}", exc)


class SARReportFiler(BaseReportFiler):
    """Load case data, map SAR fields, and write a filled SAR PDF."""

    REPORT_TYPE = "SAR"
    LEGACY_VARIANT = "legacy"
    FINCEN_ACROFORM_VARIANT = "fincen_acroform"
    PAGE2_PREFIXES = (
        "item33",
        "item34",
        "item35",
        "item36",
        "item37",
        "item38",
        "item39",
        "item40",
        "item41",
        "item42",
        "item43",
        "item44",
        "item45",
        "item46",
        "item47",
        "item48",
        "item49",
        "item50",
        "37-3",
    )
    PAGE3_FIELDS = {"item51"}
    AUTO_TEMPLATE_PATHS = (SAR_TEMPLATE_PATH, SAR_TEMPLATE_FALLBACK_PATH)

    def __init__(self, template_path: str = str(SAR_TEMPLATE_PATH), output_dir: str = str(OUTPUT_DIR)):
        # Backward-compatible fallback for environments that still use the older template name.
        self.auto_select_template = template_path == str(SAR_TEMPLATE_PATH)
        if template_path == str(SAR_TEMPLATE_PATH) and not Path(template_path).exists() and SAR_TEMPLATE_FALLBACK_PATH.exists():
            template_path = str(SAR_TEMPLATE_FALLBACK_PATH)
        super().__init__(template_path=template_path, output_dir=output_dir)
        self.template_variant = self._detect_template_variant()

    def _detect_template_variant(self) -> str:
        try:
            fields = PdfReader(str(self.template_path)).get_fields() or {}
            field_names = set(fields.keys())
        except Exception:
            return self.LEGACY_VARIANT

        if (
            "3  Individuals last name or entitys legal name a Unk" in field_names
            or "Narrative" in field_names
        ):
            return self.FINCEN_ACROFORM_VARIANT

        if any(name.startswith("item") for name in field_names):
            return self.LEGACY_VARIANT

        return self.LEGACY_VARIANT

    def _field_names_for_template(self, template_path: Path) -> set[str]:
        try:
            return set((PdfReader(str(template_path)).get_fields() or {}).keys())
        except Exception:
            return set()

    def _build_field_values(
        self,
        normalized_case: Dict[str, Any],
        template_variant: str,
        injected_narrative: str | None = None,
    ) -> Dict[str, str]:
        mapper = SARFieldMapper(normalized_case)
        field_values = mapper.map_all_fields(template_variant=template_variant)
        if injected_narrative:
            narrative_field_id = (
                "Narrative" if template_variant == self.FINCEN_ACROFORM_VARIANT else "item51"
            )
            field_values[narrative_field_id] = injected_narrative[:4000]
        return field_values

    def _choose_best_template(
        self,
        normalized_case: Dict[str, Any],
        injected_narrative: str | None = None,
    ) -> tuple[Path, str, Dict[str, str]]:
        candidates: List[Path] = []
        for path in self.AUTO_TEMPLATE_PATHS:
            if path.exists():
                candidates.append(path)
        if not candidates:
            variant = self._detect_template_variant()
            return self.template_path, variant, self._build_field_values(normalized_case, variant, injected_narrative)

        best: tuple[int, Path, str, Dict[str, str]] | None = None
        for candidate in candidates:
            self.template_path = candidate
            variant = self._detect_template_variant()
            fields = self._build_field_values(normalized_case, variant, injected_narrative)
            template_field_names = self._field_names_for_template(candidate)
            score = sum(1 for field_id in fields if field_id in template_field_names)
            if best is None or score > best[0]:
                best = (score, candidate, variant, fields)

        assert best is not None
        _, chosen_path, chosen_variant, chosen_fields = best
        return chosen_path, chosen_variant, chosen_fields

    def fill_from_dict(self, case_data: Any, injected_narrative: str | None = None) -> Dict:
        normalized_case = normalize_case_data(case_data)
        case_id = normalized_case.get("case_id", "UNKNOWN")
        report_id = str(uuid.uuid4())
        out_name = f"SAR_{case_id}_{report_id[:8]}.pdf"
        out_path = self.output_dir / out_name
        logger.info("Filling SAR PDF for case {}", case_id)

        if self.auto_select_template:
            selected_template, selected_variant, field_values = self._choose_best_template(
                normalized_case, injected_narrative
            )
            self.template_path = selected_template
            self.template_variant = selected_variant
        else:
            field_values = self._build_field_values(normalized_case, self.template_variant, injected_narrative)

        filled_count, errors = self._fill_pdf(field_values, out_path)
        template_field_count = self._template_field_count()
        attempted_fields = len(field_values)
        result = {
            "status": "success",
            "report_id": report_id,
            "case_id": case_id,
            "report_type": "SAR",
            "pdf_path": str(out_path),
            "fields_filled": filled_count,
            "attempted_fields": attempted_fields,
            "template_field_count": template_field_count,
            "fill_errors": errors,
            "template_path": str(self.template_path),
            "template_variant": self.template_variant,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log(result)
        logger.info(
            "SAR PDF saved to {} (filled={} errors={})",
            out_path,
            filled_count,
            len(errors),
        )
        return result


class CTRReportFiler(BaseReportFiler):
    """Load case data, map CTR fields, and write a filled CTR PDF."""

    REPORT_TYPE = "CTR"
    # The current template uses generic IDs split by page prefixes.
    PAGE2_PREFIXES = ("F2", "f2", "C2", "c2")
    PAGE3_PREFIXES = ("F3", "f3", "C3", "c3", "item-", "text")

    def __init__(self, template_path: str = str(CTR_TEMPLATE_PATH), output_dir: str = str(OUTPUT_DIR)):
        super().__init__(template_path=template_path, output_dir=output_dir)

    def fill_from_dict(self, case_data: Any, injected_narrative: str | None = None) -> Dict:
        del injected_narrative
        normalized_case = normalize_case_data(case_data)
        case_id = normalized_case.get("case_id", "UNKNOWN")
        report_id = str(uuid.uuid4())
        out_name = f"CTR_{case_id}_{report_id[:8]}.pdf"
        out_path = self.output_dir / out_name
        logger.info("Filling CTR PDF for case {}", case_id)

        mapper = CTRFieldMapper(normalized_case)
        field_values = mapper.map_all_fields()

        filled_count, errors = self._fill_pdf(field_values, out_path)
        template_field_count = self._template_field_count()
        attempted_fields = len(field_values)
        result = {
            "status": "success",
            "report_id": report_id,
            "case_id": case_id,
            "report_type": "CTR",
            "pdf_path": str(out_path),
            "fields_filled": filled_count,
            "attempted_fields": attempted_fields,
            "template_field_count": template_field_count,
            "fill_errors": errors,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._audit_log(result)
        logger.info(
            "CTR PDF saved to {} (filled={} errors={})",
            out_path,
            filled_count,
            len(errors),
        )
        return result


def _file_case_data(case_data: Any, report_type: str, narrative: str | None = None) -> Dict:
    normalized_case = normalize_case_data(case_data)
    rtype = (report_type or "").upper()
    if rtype == "SAR":
        filer = SARReportFiler()
        return filer.fill_from_dict(normalized_case, injected_narrative=narrative)
    if rtype == "CTR":
        filer = CTRReportFiler()
        return filer.fill_from_dict(normalized_case)
    raise ValueError(f"Unsupported report_type={report_type}")


@tool("Fill SAR PDF Form")
def fill_sar_pdf_tool(case_json_path: str) -> str:
    """Fill FinCEN SAR PDF from case JSON path and return result JSON string."""
    try:
        filer = SARReportFiler()
        result = filer.fill_from_json(case_json_path)
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, indent=2)


@tool("Fill CTR PDF Form")
def fill_ctr_pdf_tool(case_json_path: str) -> str:
    """Fill FinCEN CTR PDF from case JSON path and return result JSON string."""
    try:
        filer = CTRReportFiler()
        result = filer.fill_from_json(case_json_path)
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, indent=2)


@tool("Fill Report PDF")
def fill_report_pdf_tool(case_json_path: str, report_type: str) -> str:
    """
    Fill one or both compliance report PDFs from a case JSON file.

    Args:
        case_json_path: path to case JSON
        report_type: SAR, CTR, or BOTH
    """
    try:
        with open(case_json_path, "r", encoding="utf-8") as handle:
            case_data = json.load(handle)

        requested = (report_type or "").upper()
        if requested == "BOTH":
            report_types = ["CTR", "SAR"]
        elif requested in {"SAR", "CTR"}:
            report_types = [requested]
        else:
            return json.dumps(
                {"status": "error", "error": "report_type must be SAR, CTR, or BOTH"},
                indent=2,
            )

        results = [_file_case_data(case_data, rtype) for rtype in report_types]
        if len(results) == 1:
            return json.dumps(results[0], indent=2)
        return json.dumps({"status": "success", "reports": results}, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, indent=2)


@tool("Generate Report PDF")
def generate_report_pdf_tool(case_data: str, narrative: str, report_type: str) -> str:
    """Generate report PDF from case JSON string with optional SAR narrative override."""
    try:
        case_dict = json.loads(case_data)
        rtype = (report_type or "SAR").upper()
        if rtype == "SAR":
            result = _file_case_data(case_dict, "SAR", narrative=narrative)
        elif rtype == "CTR":
            result = _file_case_data(case_dict, "CTR")
        elif rtype == "BOTH":
            result = {
                "status": "success",
                "reports": [
                    _file_case_data(case_dict, "CTR"),
                    _file_case_data(case_dict, "SAR", narrative=narrative),
                ],
            }
        else:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Unsupported report_type={report_type}. Use SAR, CTR, or BOTH.",
                },
                indent=2,
            )
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, indent=2)


@tool("Fill PDF Form")
def fill_pdf_form_tool(template_path: str, field_data: str, output_path: str) -> str:
    """Compatibility tool: fill arbitrary form fields into a template path."""
    try:
        fields = json.loads(field_data)
        reader = PdfReader(template_path)
        writer = PdfWriter()
        writer.clone_reader_document_root(reader)
        for page in reader.pages:
            writer.add_page(page)
        for field_id, value in fields.items():
            page_idx = 0
            writer.update_page_form_field_values(writer.pages[page_idx], {field_id: str(value)})

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return json.dumps({"status": "ok", "path": output_path})
    except Exception as exc:
        logger.error("Fill PDF form failed: {}", exc)
        return json.dumps({"status": "error", "error": str(exc)})
