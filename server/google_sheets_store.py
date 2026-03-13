from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import List

import gspread
from google.oauth2.service_account import Credentials
from gspread import Worksheet
from gspread.exceptions import WorksheetNotFound


@dataclass
class SubmissionRecord:
    employee_id: str
    employee_name: str
    year: int
    quarter: str
    form_type: str
    answers: List[float]
    comments: List[str]
    submitted_at: str


class GoogleSheetsFeedbackStore:
    FEEDBACK_SHEET = "feedback"
    SUMMARY_VIEW_SHEET = "Sheet1"
    QUEUE_SHEET = "ingestion_queue"
    SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)

    HEADER = [
        "record_type",
        "employee_id",
        "employee_name",
        "year",
        "quarter",
        "form_type",
        "answers_json",
        "comments_json",
        "submitted_at",
        "manager_email",
        "client_email",
        "peer_1",
        "peer_2",
        "self_avg",
        "client_avg",
        "peer_avg",
        "manager_avg",
        "total_average",
        "client_diff_gt_1",
        "manager_diff_gt_1",
        "client_diff_gt_05",
        "manager_diff_gt_05",
    ]
    SUMMARY_VIEW_HEADER = [
        "Emp ID",
        "Name",
        "Manager Email",
        "Client Email",
        "Peer 1",
        "Peer 2",
        "Self",
        "Client",
        "Peer",
        "Manager",
        "Total Average",
        "Client Difference >1",
        "Manager Difference >1",
        "Client Difference >0.5",
        "Manager Difference >0.5",
    ]
    QUEUE_HEADER = [
        "event_id",
        "status",
        "attempt_count",
        "next_retry_at",
        "created_at",
        "updated_at",
        "last_error",
        "payload_json",
    ]

    def __init__(
        self,
        spreadsheet_id: str,
        service_account_file: str | None = None,
        service_account_json: str | None = None,
        summary_view_sheet: str = SUMMARY_VIEW_SHEET,
    ) -> None:
        creds = self._build_credentials(service_account_file, service_account_json)
        client = gspread.authorize(creds)
        self._sheet = client.open_by_key(spreadsheet_id)
        self.summary_view_sheet = summary_view_sheet
        self._lock = Lock()
        self._feedback_ws = self._ensure_worksheet(self.FEEDBACK_SHEET, self.HEADER, rows=5000, cols=30)
        self._summary_view_ws = self._ensure_worksheet(
            self.summary_view_sheet, self.SUMMARY_VIEW_HEADER, rows=3000, cols=20
        )
        self._queue_ws = self._ensure_worksheet(self.QUEUE_SHEET, self.QUEUE_HEADER, rows=5000, cols=12)

    def _build_credentials(
        self, service_account_file: str | None, service_account_json: str | None
    ) -> Credentials:
        if service_account_file:
            return Credentials.from_service_account_file(service_account_file, scopes=self.SCOPES)
        if service_account_json:
            raw = json.loads(service_account_json)
            return Credentials.from_service_account_info(raw, scopes=self.SCOPES)
        raise ValueError(
            "Missing Google credentials. Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON."
        )

    def _ensure_worksheet(self, title: str, header: List[str], rows: int, cols: int) -> Worksheet:
        try:
            ws = self._sheet.worksheet(title)
        except WorksheetNotFound:
            ws = self._sheet.add_worksheet(title=title, rows=rows, cols=cols)
            ws.append_row(header, value_input_option="RAW")
            return ws

        first_row = ws.row_values(1)
        if not first_row:
            ws.append_row(header, value_input_option="RAW")
            return ws

        normalized_existing = [str(h).strip() for h in first_row[: len(header)]]
        normalized_expected = [str(h).strip() for h in header]
        if normalized_existing != normalized_expected:
            raise ValueError(
                f"Worksheet '{title}' header mismatch for single-sheet mode. "
                f"Expected {header}, found {first_row[:len(header)]}."
            )
        return ws

    def append_submission(self, record: SubmissionRecord) -> None:
        with self._lock:
            self._feedback_ws.append_row(
                [
                    "submission",
                    record.employee_id,
                    record.employee_name,
                    str(record.year),
                    record.quarter,
                    record.form_type,
                    json.dumps(record.answers),
                    json.dumps(record.comments),
                    record.submitted_at,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                value_input_option="RAW",
            )

    def enqueue_event(self, payload: dict, created_at: str, event_id: str | None = None) -> tuple[str, bool]:
        event_id = (event_id or str(uuid.uuid4())).strip()
        if not event_id:
            event_id = str(uuid.uuid4())
        with self._lock:
            rows = self._queue_ws.get_all_values()
            for row in rows[1:]:
                if row and row[0].strip() == event_id:
                    return event_id, False
            self._queue_ws.append_row(
                [event_id, "pending", "0", "", created_at, created_at, "", json.dumps(payload)],
                value_input_option="RAW",
            )
        return event_id, True

    def claim_due_events(self, now_iso_value: str, limit: int = 20) -> List[dict]:
        claimed: List[dict] = []
        with self._lock:
            rows = self._queue_ws.get_all_values()
            for idx, row in enumerate(rows[1:], start=2):
                if len(claimed) >= limit:
                    break
                if len(row) < 8:
                    continue
                status = row[1].strip().lower()
                next_retry_at = row[3].strip()
                if status not in {"pending", "retry"}:
                    continue
                if next_retry_at and next_retry_at > now_iso_value:
                    continue
                attempt_count = int(row[2] or "0") + 1
                self._queue_ws.update(
                    f"B{idx}:F{idx}",
                    [["processing", str(attempt_count), next_retry_at, row[4], now_iso_value]],
                    value_input_option="RAW",
                )
                payload = json.loads(row[7] or "{}")
                claimed.append({"event_id": row[0].strip(), "attempt_count": attempt_count, "payload": payload})
        return claimed

    def mark_event_processed(self, event_id: str, now_iso_value: str) -> None:
        self._update_event_status(event_id, "processed", now_iso_value, "", "")

    def mark_event_retry(self, event_id: str, now_iso_value: str, next_retry_at: str, error: str) -> None:
        self._update_event_status(event_id, "retry", now_iso_value, next_retry_at, error)

    def mark_event_failed(self, event_id: str, now_iso_value: str, error: str) -> None:
        self._update_event_status(event_id, "failed", now_iso_value, "", error)

    def _update_event_status(
        self, event_id: str, status: str, now_iso_value: str, next_retry_at: str, error: str
    ) -> None:
        with self._lock:
            rows = self._queue_ws.get_all_values()
            for idx, row in enumerate(rows[1:], start=2):
                if not row or row[0].strip() != event_id:
                    continue
                attempt_count = row[2] if len(row) > 2 else "0"
                created_at = row[4] if len(row) > 4 else now_iso_value
                self._queue_ws.update(
                    f"B{idx}:G{idx}",
                    [[status, attempt_count, next_retry_at, created_at, now_iso_value, (error or "")[:1000]]],
                    value_input_option="RAW",
                )
                break

    def get_queue_stats(self) -> dict:
        stats = {"pending": 0, "processing": 0, "processed": 0, "retry": 0, "failed": 0, "total": 0}
        rows = self._queue_ws.get_all_values()
        for row in rows[1:]:
            if len(row) < 2:
                continue
            status = row[1].strip().lower()
            if status in stats:
                stats[status] += 1
            stats["total"] += 1
        return stats

    def get_employee_name(self, employee_id: str) -> str | None:
        values = self._feedback_ws.get_all_values()
        for row in values[1:]:
            if len(row) < 3:
                continue
            if row[1].strip() == employee_id:
                return row[2].strip() or None
        return None

    def get_employee_quarter(self, employee_id: str, year: int, quarter: str) -> List[SubmissionRecord]:
        results: List[SubmissionRecord] = []
        values = self._feedback_ws.get_all_values()
        for row in values[1:]:
            if len(row) < 9:
                continue
            if row[0].strip() != "submission":
                continue
            if row[1].strip() != employee_id:
                continue
            if row[3].strip() != str(year):
                continue
            if row[4].strip() != quarter:
                continue
            results.append(self._row_to_record(row))
        return results

    def list_submission_status(self, year: int, quarter: str) -> List[dict]:
        by_employee: dict[str, dict] = {}
        values = self._feedback_ws.get_all_values()
        for row in values[1:]:
            if len(row) < 22:
                continue
            record_type = row[0].strip()
            row_emp = row[1].strip()
            if not row_emp:
                continue
            row_name = (row[2] or "").strip() or row_emp
            row_year = row[3].strip()
            row_quarter = row[4].strip()
            if row_year != str(year) or row_quarter != quarter:
                continue

            item = by_employee.setdefault(
                row_emp,
                {
                    "employee_email": row_emp,
                    "employee_name": row_name,
                    "submitted_forms": set(),
                    "manager_email": "",
                    "client_email": "",
                },
            )

            if record_type == "submission":
                form_type = row[5].strip()
                if form_type:
                    item["submitted_forms"].add(form_type)
            elif record_type == "summary":
                item["manager_email"] = row[9].strip() if len(row) > 9 else ""
                item["client_email"] = row[10].strip() if len(row) > 10 else ""

        results: List[dict] = []
        for _, item in by_employee.items():
            results.append(
                {
                    "employee_email": item["employee_email"],
                    "employee_name": item["employee_name"],
                    "submitted_forms": sorted(item["submitted_forms"]),
                    "manager_email": item["manager_email"],
                    "client_email": item["client_email"],
                }
            )
        return results

    def _row_to_record(self, row: List[str]) -> SubmissionRecord:
        try:
            answers = [float(v) for v in json.loads(row[6] or "[]")]
        except (ValueError, TypeError, json.JSONDecodeError):
            answers = []
        try:
            comments = [str(v) for v in json.loads(row[7] or "[]")]
        except (TypeError, json.JSONDecodeError):
            comments = []
        return SubmissionRecord(
            employee_id=row[1].strip(),
            employee_name=(row[2] or "Unknown").strip(),
            year=int(row[3]),
            quarter=row[4].strip(),
            form_type=row[5].strip(),
            answers=answers,
            comments=comments,
            submitted_at=(row[8] or "").strip(),
        )

    def upsert_summary_row(
        self,
        employee_id: str,
        employee_name: str,
        manager_email: str,
        client_email: str,
        peer_1: str,
        peer_2: str,
        self_avg: float | None,
        client_avg: float | None,
        peer_avg: float | None,
        manager_avg: float | None,
        total_average: float,
        client_diff_gt_1: float | str,
        manager_diff_gt_1: float | str,
        client_diff_gt_05: float | str,
        manager_diff_gt_05: float | str,
        year: int,
        quarter: str,
    ) -> None:
        row_data = [
            "summary",
            employee_id,
            employee_name,
            str(year),
            quarter,
            "",
            "",
            "",
            now_iso(),
            manager_email or "",
            client_email or "",
            peer_1 or "",
            peer_2 or "",
            self_avg if self_avg is not None else "",
            client_avg if client_avg is not None else "",
            peer_avg if peer_avg is not None else "",
            manager_avg if manager_avg is not None else "",
            total_average,
            client_diff_gt_1,
            manager_diff_gt_1,
            client_diff_gt_05,
            manager_diff_gt_05,
        ]

        with self._lock:
            rows = self._feedback_ws.get_all_values()
            target_row = None
            for idx, row in enumerate(rows[1:], start=2):
                if len(row) < 5:
                    continue
                if row[0].strip() != "summary":
                    continue
                if row[1].strip() == employee_id and row[3].strip() == str(year) and row[4].strip() == quarter:
                    target_row = idx
                    break

            if target_row:
                self._feedback_ws.update(
                    f"A{target_row}:V{target_row}",
                    [row_data],
                    value_input_option="RAW",
                )
            else:
                self._feedback_ws.append_row(row_data, value_input_option="RAW")

            self._upsert_summary_view_row(
                employee_id=employee_id,
                employee_name=employee_name,
                manager_email=manager_email,
                client_email=client_email,
                peer_1=peer_1,
                peer_2=peer_2,
                self_avg=self_avg,
                client_avg=client_avg,
                peer_avg=peer_avg,
                manager_avg=manager_avg,
                total_average=total_average,
                client_diff_gt_1=client_diff_gt_1,
                manager_diff_gt_1=manager_diff_gt_1,
                client_diff_gt_05=client_diff_gt_05,
                manager_diff_gt_05=manager_diff_gt_05,
            )

    def _is_kpi_row(self, row: List[str]) -> bool:
        name = row[1].strip() if len(row) > 1 else ""
        if name:
            return False
        for idx in range(6, 15):
            if len(row) > idx and str(row[idx]).strip():
                return True
        label = row[9].strip().lower() if len(row) > 9 else ""
        return label in {"total average", ">=4.5", ">3.5 and <4.5", "<=3.5 and >2", ">=4", ">3 and <4"}

    def _upsert_summary_view_row(
        self,
        employee_id: str,
        employee_name: str,
        manager_email: str,
        client_email: str,
        peer_1: str,
        peer_2: str,
        self_avg: float | None,
        client_avg: float | None,
        peer_avg: float | None,
        manager_avg: float | None,
        total_average: float,
        client_diff_gt_1: float | str,
        manager_diff_gt_1: float | str,
        client_diff_gt_05: float | str,
        manager_diff_gt_05: float | str,
    ) -> None:
        rows = self._summary_view_ws.get_all_values()
        target_row = None
        for idx, row in enumerate(rows[1:], start=2):
            row_emp = row[0].strip() if len(row) > 0 else ""
            row_name = row[1].strip() if len(row) > 1 else ""
            if row_emp and row_emp == employee_id:
                target_row = idx
                break
            if not row_emp and row_name and row_name == employee_name:
                target_row = idx
                break

        row_data = [
            employee_id,
            employee_name,
            manager_email or "",
            client_email or "",
            peer_1 or "",
            peer_2 or "",
            self_avg if self_avg is not None else "",
            client_avg if client_avg is not None else "",
            peer_avg if peer_avg is not None else "",
            manager_avg if manager_avg is not None else "",
            total_average,
            client_diff_gt_1,
            manager_diff_gt_1,
            client_diff_gt_05,
            manager_diff_gt_05,
        ]

        if target_row:
            self._summary_view_ws.update(
                f"A{target_row}:O{target_row}",
                [row_data],
                value_input_option="RAW",
            )
            return

        insert_at = len(rows) + 1
        for idx, row in enumerate(rows[1:], start=2):
            if self._is_kpi_row(row):
                insert_at = idx
                break
        self._summary_view_ws.insert_row(row_data, index=insert_at, value_input_option="RAW")


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
