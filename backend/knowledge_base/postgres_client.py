from __future__ import annotations

import enum
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import (
    BOOLEAN,
    TIMESTAMP,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    TEXT,
    func,
    create_engine,
)
from sqlalchemy.engine.url import make_url
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from backend.config.settings import settings


@contextmanager
def session_scope(engine):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class Base(DeclarativeBase):
    pass


class Severity(enum.Enum):
    critical = "critical"
    warning = "warning"
    quality = "quality"


class RiskLevel(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class JobStatusEnum(enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class ReportSchema(Base):
    __tablename__ = "report_schemas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(BOOLEAN, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    validation_rules = relationship("ValidationRule", back_populates="schema", cascade="all, delete")
    field_mappings = relationship("FieldMapping", back_populates="schema", cascade="all, delete")


class ValidationRule(Base):
    __tablename__ = "validation_rules"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String(64), ForeignKey("report_schemas.report_type"), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rule_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    severity: Mapped[Severity] = mapped_column(SQLEnum(Severity), nullable=False)
    description: Mapped[str] = mapped_column(TEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    schema = relationship("ReportSchema", back_populates="validation_rules")


class FieldMapping(Base):
    __tablename__ = "field_mappings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String(64), ForeignKey("report_schemas.report_type"), nullable=False, index=True)
    source_field: Mapped[str] = mapped_column(String(128), nullable=False)
    target_field: Mapped[str] = mapped_column(String(128), nullable=False)
    transformation: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    schema = relationship("ReportSchema", back_populates="field_mappings")


class RiskIndicator(Base):
    __tablename__ = "risk_indicators"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    detection_logic: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(SQLEnum(RiskLevel), nullable=False)
    regulatory_reference: Mapped[str] = mapped_column(TEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=True)
    details: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=func.now(), nullable=False)


class JobStatus(Base):
    __tablename__ = "job_status"
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[JobStatusEnum] = mapped_column(SQLEnum(JobStatusEnum), nullable=False, default=JobStatusEnum.pending)
    current_agent: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input_data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str] = mapped_column(TEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


Index("idx_report_type", ReportSchema.report_type)
Index("idx_validation_report", ValidationRule.report_type)
Index("idx_field_mapping_report", FieldMapping.report_type)
Index("idx_job_status_status", JobStatus.status)


class PostgreSQLClient:
    def __init__(self, database_url: Optional[str] = None):
        database_url = database_url or settings.DATABASE_URL
        self.engine = create_engine(database_url, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, future=True)
        parsed = make_url(database_url)
        logger.debug(
            "PostgreSQL client configured for user={} host={} db={}",
            parsed.username,
            parsed.host,
            parsed.database,
        )

    def create_tables(self) -> None:
        Base.metadata.create_all(bind=self.engine)
        logger.info("PostgreSQL schema created/verified")

    def _session(self):
        return self.SessionLocal()

    def get_schema(self, report_type: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            schema = session.query(ReportSchema).filter_by(report_type=report_type, is_active=True).first()
            return schema.schema_json if schema else None

    def add_schema(self, report_type: str, version: str, schema_json: Dict[str, Any], effective_date: str, is_active: bool = True) -> uuid.UUID:
        with self._session() as session:
            record = ReportSchema(
                report_type=report_type,
                version=version,
                schema_json=schema_json,
                effective_date=datetime.fromisoformat(effective_date).date(),
                is_active=is_active,
            )
            session.add(record)
            session.flush()
            logger.info("Schema stored for report_type=%s", report_type)
            return record.id

    def add_validation_rule(self, rule: Dict[str, Any]) -> uuid.UUID:
        with self._session() as session:
            record = ValidationRule(
                report_type=rule["report_type"],
                rule_id=rule["rule_id"],
                rule_json=rule["rule_json"],
                severity=Severity(rule["severity"]),
                description=rule.get("description"),
            )
            session.add(record)
            session.flush()
            logger.debug("Validation rule %s stored", record.rule_id)
            return record.id

    def get_validation_rules(self, report_type: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            rules = (
                session.query(ValidationRule)
                .filter_by(report_type=report_type)
                .order_by(ValidationRule.created_at)
                .all()
            )
            return [
                {
                    "rule_id": rule.rule_id,
                    "severity": rule.severity.value,
                    "rule_json": rule.rule_json,
                    "description": rule.description,
                }
                for rule in rules
            ]

    def get_field_mappings(self, report_type: str) -> List[Dict[str, Any]]:
        with self._session() as session:
            mapping = (
                session.query(FieldMapping)
                .filter_by(report_type=report_type)
                .order_by(FieldMapping.created_at)
                .all()
            )
            return [
                {
                    "source_field": item.source_field,
                    "target_field": item.target_field,
                    "transformation": item.transformation,
                }
                for item in mapping
            ]

    def get_risk_indicators(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._session() as session:
            query = session.query(RiskIndicator)
            if category:
                query = query.filter_by(category=category)
            indicators = query.order_by(RiskIndicator.created_at).all()
            return [
                {
                    "indicator_id": indicator.indicator_id,
                    "name": indicator.name,
                    "category": indicator.category,
                    "detection_logic": indicator.detection_logic,
                    "risk_level": indicator.risk_level.value,
                    "regulatory_reference": indicator.regulatory_reference,
                }
                for indicator in indicators
            ]

    def log_audit(self, session_id: uuid.UUID, action: str, agent_name: str, entity_type: str, entity_id: Optional[str], details: Dict[str, Any]) -> uuid.UUID:
        with self._session() as session:
            record = AuditLog(
                session_id=session_id,
                action=action,
                agent_name=agent_name,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
            )
            session.add(record)
            session.flush()
            logger.debug("Audit log stored: %s", record.id)
            return record.id

    def create_job(self, job_id: str, status: str = "pending", input_data: Optional[Dict[str, Any]] = None) -> uuid.UUID:
        with self._session() as session:
            record = JobStatus(
                job_id=uuid.UUID(job_id),
                status=JobStatusEnum[status],
                input_data=input_data,
                progress=0,
            )
            session.add(record)
            session.flush()
            logger.info("Job %s created", job_id)
            return record.job_id

    def update_job_status(
        self,
        job_id: str,
        status: str,
        current_agent: Optional[str] = None,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._session() as session:
            record = session.get(JobStatus, uuid.UUID(job_id))
            if not record:
                raise ValueError("Job not found")
            record.status = JobStatusEnum[status]
            if current_agent:
                record.current_agent = current_agent
            if progress is not None:
                record.progress = progress
            if result:
                record.result = result
            if error:
                record.error_message = error
            session.add(record)
            logger.debug("Job %s updated to %s", job_id, status)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            record = session.get(JobStatus, uuid.UUID(job_id))
            if not record:
                return None
            return {
                "job_id": str(record.job_id),
                "status": record.status.value,
                "current_agent": record.current_agent,
                "progress": record.progress,
                "result": record.result,
                "error_message": record.error_message,
            }

    def add_field_mapping(self, data: Dict[str, Any]) -> uuid.UUID:
        with self._session() as session:
            record = FieldMapping(
                report_type=data["report_type"],
                source_field=data["source_field"],
                target_field=data["target_field"],
                transformation=data.get("transformation"),
            )
            session.add(record)
            session.flush()
            logger.debug("Field mapping stored %s -> %s", record.source_field, record.target_field)
            return record.id

    def add_risk_indicator(self, data: Dict[str, Any]) -> uuid.UUID:
        with self._session() as session:
            record = RiskIndicator(
                indicator_id=data["indicator_id"],
                name=data["name"],
                category=data["category"],
                detection_logic=data.get("detection_logic", {}),
                risk_level=RiskLevel(data["risk_level"]),
                regulatory_reference=data.get("regulatory_reference"),
            )
            session.add(record)
            session.flush()
            logger.debug("Risk indicator stored %s", record.indicator_id)
            return record.id

    # Placeholder for Alembic migrations
    def run_migrations(self) -> None:
        # Alembic environment should be configured separately
        pass
