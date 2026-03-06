from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv
from utils import (
    assess_performance,
    avg,
    collect_by_form,
    compute_quarter_score,
    discrepancy_message,
    extract_comment_summary,
    submission_key,
)

app = FastAPI(title="HR Employee Feedback Automation")
load_dotenv()

FormType = Literal["self", "manager", "client", "peer"]
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local_excel").strip().lower()
STORE_INFO: Dict[str, str] = {"storage": STORAGE_BACKEND}
PROCESS_INLINE_ON_FEEDBACK = os.getenv("PROCESS_INLINE_ON_FEEDBACK", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}
STORE_INFO["process_inline_on_feedback"] = str(PROCESS_INLINE_ON_FEEDBACK).lower()

if STORAGE_BACKEND == "google_sheets":
    from google_sheets_store import GoogleSheetsFeedbackStore, SubmissionRecord, now_iso

    SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or None
    SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip() or None
    SUMMARY_VIEW_SHEET = os.getenv("HR_SUMMARY_SHEET", "Sheet1").strip()

    if not SPREADSHEET_ID:
        raise RuntimeError("Missing GOOGLE_SHEETS_SPREADSHEET_ID environment variable")

    store = GoogleSheetsFeedbackStore(
        spreadsheet_id=SPREADSHEET_ID,
        service_account_file=SERVICE_ACCOUNT_FILE,
        service_account_json=SERVICE_ACCOUNT_JSON,
        summary_view_sheet=SUMMARY_VIEW_SHEET,
    )
    STORE_INFO["spreadsheet_id"] = SPREADSHEET_ID
    STORE_INFO["sheet_name"] = GoogleSheetsFeedbackStore.FEEDBACK_SHEET
    STORE_INFO["summary_view_sheet"] = SUMMARY_VIEW_SHEET
elif STORAGE_BACKEND == "local_excel":
    from excel_store import ExcelFeedbackStore, SubmissionRecord, now_iso

    WORKBOOK_PATH = os.getenv("FEEDBACK_WORKBOOK_PATH", "server/data/feedback_store.xlsx").strip()
    SUMMARY_VIEW_SHEET = os.getenv("HR_SUMMARY_SHEET", "Sheet1").strip()
    store = ExcelFeedbackStore(WORKBOOK_PATH, summary_view_sheet=SUMMARY_VIEW_SHEET)
    STORE_INFO["workbook_path"] = WORKBOOK_PATH
    STORE_INFO["sheet_name"] = ExcelFeedbackStore.FEEDBACK_SHEET
    STORE_INFO["summary_view_sheet"] = SUMMARY_VIEW_SHEET
else:
    raise RuntimeError("Invalid STORAGE_BACKEND. Use 'local_excel' or 'google_sheets'.")


class FeedbackSubmission(BaseModel):
    employee_id: str | None = None
    employee_email: str = Field(..., min_length=3)
    employee_name: str = Field(..., min_length=1)
    manager_email: str | None = None
    client_email: str | None = None
    peer_1_name: str | None = None
    peer_2_name: str | None = None
    quarter: Literal["Q1", "Q2", "Q3", "Q4"]
    year: int = Field(..., ge=2000, le=2100)
    form_type: FormType
    answers: List[float] = Field(..., min_length=1)
    comments: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scores(self) -> "FeedbackSubmission":
        for score in self.answers:
            if score < 1 or score > 5:
                raise ValueError("All scores must be between 1 and 5")
        return self


class FollowupPlanRequest(BaseModel):
    start_date: date
    deadline: date
    minimum_followups: int = Field(default=5, ge=5)


class PerformanceAssessment(BaseModel):
    level: str
    strengths: List[str]
    improvements: List[str]


class QuarterSummary(BaseModel):
    employee_id: str
    employee_email: str | None = None
    employee_name: str
    year: int
    quarter: str
    average_by_form: Dict[str, float]
    appraisal_score: float
    discrepancy_alert: bool
    discrepancy_message: str | None = None
    performance_level: str
    positive_summary: List[str]
    negative_summary: List[str]


class QueueProcessRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    max_attempts: int = Field(default=10, ge=1, le=50)


def _difference_fields(averages: Dict[str, float]) -> Dict[str, float | str]:
    self_avg = averages.get("self")
    client_avg = averages.get("client")
    manager_avg = averages.get("manager")

    def flag(compare_val: float | None, threshold: float) -> float | str:
        if self_avg is None or compare_val is None:
            return ""
        return compare_val if abs(self_avg - compare_val) > threshold else ""

    return {
        "client_diff_gt_1": flag(client_avg, 1.0),
        "manager_diff_gt_1": flag(manager_avg, 1.0),
        "client_diff_gt_05": flag(client_avg, 0.5),
        "manager_diff_gt_05": flag(manager_avg, 0.5),
    }


def _normalize_employee_key(employee_id: str | None, employee_email: str | None) -> str:
    if employee_email and employee_email.strip():
        return employee_email.strip().lower()
    if employee_id and employee_id.strip():
        return employee_id.strip()
    raise HTTPException(status_code=400, detail="Either employee_email or employee_id is required")


def _retry_time_iso(attempt_count: int) -> str:
    # Exponential backoff capped to 1 hour.
    delay_seconds = min(60 * (2 ** max(attempt_count - 1, 0)), 3600)
    return (datetime.utcnow() + timedelta(seconds=delay_seconds)).replace(microsecond=0).isoformat() + "Z"


def _quarter_summary(employee_key: str, year: int, quarter: str) -> QuarterSummary:
    submissions = store.get_employee_quarter(employee_key, year, quarter)
    if not submissions:
        raise HTTPException(status_code=404, detail="No feedback found for this employee quarter")

    as_domain_items = [
        SimpleNamespace(form_type=item.form_type, answers=item.answers, comments=item.comments)
        for item in submissions
    ]

    averages_raw = collect_by_form(as_domain_items)
    averages = {form: avg(scores) for form, scores in averages_raw.items()}

    appraisal_score = compute_quarter_score(averages)
    performance = PerformanceAssessment(**assess_performance(appraisal_score))
    discrepancy_msg = discrepancy_message(averages)
    positive_summary, negative_summary = extract_comment_summary(as_domain_items)

    return QuarterSummary(
        employee_id=employee_key,
        employee_email=employee_key if "@" in employee_key else None,
        employee_name=store.get_employee_name(employee_key) or "Unknown",
        year=year,
        quarter=quarter,
        average_by_form=averages,
        appraisal_score=appraisal_score,
        discrepancy_alert=discrepancy_msg is not None,
        discrepancy_message=discrepancy_msg,
        performance_level=performance.level,
        positive_summary=positive_summary,
        negative_summary=negative_summary,
    )


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok", **STORE_INFO}


def _process_feedback_payload(payload: FeedbackSubmission) -> dict:
    # Business processing path: write normalized submission, compute quarter summary,
    # and mirror summary values to HR-facing sheet.
    employee_key = _normalize_employee_key(payload.employee_id, payload.employee_email)
    key = submission_key(payload.year, payload.quarter)
    record = SubmissionRecord(
        employee_id=employee_key,
        employee_name=payload.employee_name,
        year=payload.year,
        quarter=payload.quarter,
        form_type=payload.form_type,
        answers=payload.answers,
        comments=payload.comments,
        submitted_at=now_iso(),
    )
    store.append_submission(record)
    summary_status = "updated"
    try:
        summary = _quarter_summary(employee_key, payload.year, payload.quarter)
        diffs = _difference_fields(summary.average_by_form)
        if hasattr(store, "upsert_summary_row"):
            store.upsert_summary_row(
                employee_id=employee_key,
                employee_name=payload.employee_name,
                manager_email=payload.manager_email or "",
                client_email=payload.client_email or "",
                peer_1=payload.peer_1_name or "",
                peer_2=payload.peer_2_name or "",
                self_avg=summary.average_by_form.get("self"),
                client_avg=summary.average_by_form.get("client"),
                peer_avg=summary.average_by_form.get("peer"),
                manager_avg=summary.average_by_form.get("manager"),
                total_average=summary.appraisal_score,
                client_diff_gt_1=diffs["client_diff_gt_1"],
                manager_diff_gt_1=diffs["manager_diff_gt_1"],
                client_diff_gt_05=diffs["client_diff_gt_05"],
                manager_diff_gt_05=diffs["manager_diff_gt_05"],
                year=payload.year,
                quarter=payload.quarter,
            )
    except HTTPException as exc:
        # Partial form submissions are valid; summary requires self + manager/client.
        if exc.status_code in (400, 404):
            summary_status = "pending_required_forms"
        else:
            raise

    return {
        "status": "processed",
        "employee_id": employee_key,
        "employee_email": employee_key if "@" in employee_key else None,
        "key": key,
        "form_type": payload.form_type,
        "summary_status": summary_status,
    }


@app.post("/feedback")
def submit_feedback(payload: FeedbackSubmission) -> dict:
    # API ingestion path. Always capture event in durable queue first.
    # Processing can happen inline (optional) or via /queue/process worker.
    now = now_iso()
    event_payload = payload.model_dump()
    event_id, created = store.enqueue_event(event_payload, now)

    if not created:
        return {
            "status": "recorded",
            "event_id": event_id,
            "queue_status": "duplicate_ignored",
            "message": "Event already exists in queue.",
        }

    if not PROCESS_INLINE_ON_FEEDBACK:
        return {
            "status": "recorded",
            "event_id": event_id,
            "queue_status": "pending",
            "message": "Submission captured in queue.",
        }

    # Try inline processing first for low latency; queue ensures durability on failure.
    try:
        result = _process_feedback_payload(payload)
        store.mark_event_processed(event_id, now_iso())
        return {
            "status": "recorded",
            "event_id": event_id,
            "queue_status": "processed",
            **{k: v for k, v in result.items() if k != "status"},
        }
    except Exception as exc:
        store.mark_event_retry(event_id, now_iso(), _retry_time_iso(1), str(exc))
        return {
            "status": "recorded",
            "event_id": event_id,
            "queue_status": "retry_scheduled",
            "message": "Submission captured in queue and will be retried.",
        }


@app.post("/queue/process")
def process_queue(payload: QueueProcessRequest) -> dict:
    # Worker path. Claims due queue events and processes them with retry/fail handling.
    now = now_iso()
    events = store.claim_due_events(now, payload.limit)
    processed = 0
    retried = 0
    failed = 0
    details: List[dict] = []

    for event in events:
        event_id = event["event_id"]
        attempt_count = int(event.get("attempt_count", 1))
        try:
            feedback_payload = FeedbackSubmission(**event["payload"])
            result = _process_feedback_payload(feedback_payload)
            store.mark_event_processed(event_id, now_iso())
            processed += 1
            details.append({"event_id": event_id, "status": "processed", "summary_status": result["summary_status"]})
        except Exception as exc:
            if attempt_count >= payload.max_attempts:
                store.mark_event_failed(event_id, now_iso(), str(exc))
                failed += 1
                details.append({"event_id": event_id, "status": "failed"})
            else:
                store.mark_event_retry(event_id, now_iso(), _retry_time_iso(attempt_count), str(exc))
                retried += 1
                details.append({"event_id": event_id, "status": "retry"})

    return {
        "claimed": len(events),
        "processed": processed,
        "retried": retried,
        "failed": failed,
        "details": details,
    }


@app.get("/queue/stats")
def queue_stats() -> dict:
    if not hasattr(store, "get_queue_stats"):
        raise HTTPException(status_code=501, detail="Queue stats not supported by current storage backend")
    return store.get_queue_stats()


@app.get("/employees/{employee_id}/quarters/{year}/{quarter}", response_model=QuarterSummary)
def get_employee_quarter(employee_id: str, year: int, quarter: Literal["Q1", "Q2", "Q3", "Q4"]) -> QuarterSummary:
    employee_key = _normalize_employee_key(employee_id, employee_id if "@" in employee_id else None)
    return _quarter_summary(employee_key, year, quarter)


@app.get("/employees/{employee_id}/yearly/{year}")
def get_employee_yearly(employee_id: str, year: int) -> dict:
    employee_key = _normalize_employee_key(employee_id, employee_id if "@" in employee_id else None)
    quarter_scores: Dict[str, float] = {}
    quarter_summaries: Dict[str, dict] = {}

    for quarter in ["Q1", "Q2", "Q3", "Q4"]:
        try:
            summary = _quarter_summary(employee_key, year, quarter)
        except HTTPException:
            continue
        quarter_scores[quarter] = summary.appraisal_score
        quarter_summaries[quarter] = summary.model_dump()

    if not quarter_scores:
        raise HTTPException(status_code=404, detail="No yearly data found")

    yearly_score = avg(list(quarter_scores.values()))
    performance = PerformanceAssessment(**assess_performance(yearly_score))

    employee_name = store.get_employee_name(employee_key) or employee_key
    hr_email = {
        "to": "hr-team@company.com",
        "subject": f"{year} Annual Performance Report: {employee_name} ({employee_key})",
        "body": (
            f"Yearly score: {yearly_score}/5. "
            f"Performance level: {performance.level}. "
            f"Key strengths: {', '.join(performance.strengths)}. "
            f"Improvement areas: {', '.join(performance.improvements)}."
        ),
    }

    return {
        "employee_id": employee_key,
        "employee_email": employee_key if "@" in employee_key else None,
        "employee_name": employee_name,
        "year": year,
        "quarter_scores": quarter_scores,
        "yearly_score": yearly_score,
        "performance_assessment": performance.model_dump(),
        "quarter_summaries": quarter_summaries,
        "hr_email_preview": hr_email,
    }


@app.post("/followups/plan")
def generate_followup_plan(payload: FollowupPlanRequest) -> dict:
    if payload.deadline <= payload.start_date:
        raise HTTPException(status_code=400, detail="deadline must be after start_date")

    total_days = (payload.deadline - payload.start_date).days
    slots = payload.minimum_followups + 1
    gap = max(total_days // slots, 1)

    followup_dates = [
        payload.start_date + timedelta(days=gap * i)
        for i in range(1, payload.minimum_followups + 1)
    ]

    followup_dates = [d for d in followup_dates if d < payload.deadline]

    while len(followup_dates) < payload.minimum_followups:
        candidate = payload.deadline - timedelta(days=(payload.minimum_followups - len(followup_dates)))
        if candidate <= payload.start_date:
            candidate = payload.start_date + timedelta(days=len(followup_dates) + 1)
        if candidate not in followup_dates and candidate < payload.deadline:
            followup_dates.append(candidate)
        else:
            break

    followup_dates = sorted(followup_dates)[: payload.minimum_followups]

    return {
        "start_date": payload.start_date,
        "deadline": payload.deadline,
        "minimum_followups": payload.minimum_followups,
        "planned_followup_dates": followup_dates,
    }


@app.get("/employees/{employee_id}/alerts/{year}/{quarter}")
def get_quarter_alert(employee_id: str, year: int, quarter: Literal["Q1", "Q2", "Q3", "Q4"]) -> dict:
    employee_key = _normalize_employee_key(employee_id, employee_id if "@" in employee_id else None)
    summary = _quarter_summary(employee_key, year, quarter)

    if not summary.discrepancy_alert:
        return {
            "alert_required": False,
            "message": "No discrepancy alert needed",
        }

    return {
        "alert_required": True,
        "to": "hr-team@company.com",
        "subject": f"Rating discrepancy alert for {summary.employee_name} ({employee_key})",
        "message": summary.discrepancy_message,
    }


@app.get("/employees/{employee_id}/quarters/{year}/{quarter}/hr-mail")
def get_quarter_hr_mail(employee_id: str, year: int, quarter: Literal["Q1", "Q2", "Q3", "Q4"]) -> dict:
    employee_key = _normalize_employee_key(employee_id, employee_id if "@" in employee_id else None)
    summary = _quarter_summary(employee_key, year, quarter)

    return {
        "to": "hr-team@company.com",
        "subject": f"Quarterly Performance Report {year}-{quarter}: {summary.employee_name} ({employee_key})",
        "body": (
            f"Appraisal score: {summary.appraisal_score}/5. "
            f"Performance level: {summary.performance_level}. "
            f"Discrepancy alert: {summary.discrepancy_alert}. "
            f"Positive highlights: {', '.join(summary.positive_summary) if summary.positive_summary else 'None'}. "
            f"Improvement highlights: {', '.join(summary.negative_summary) if summary.negative_summary else 'None'}."
        ),
    }


@app.get("/employees/{employee_id}/yearly/{year}/hr-mail")
def get_yearly_hr_mail(employee_id: str, year: int) -> dict:
    yearly_data = get_employee_yearly(employee_id, year)
    return yearly_data["hr_email_preview"]
