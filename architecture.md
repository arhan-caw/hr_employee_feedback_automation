# HR Employee Feedback Automation - Architecture (Google Sheets First)

## 1) Goal
Use a parallel Google Sheet as the pilot system of record, automate collection and scoring logic in FastAPI, and generate quarterly/yearly HR reporting payloads.

## 2) End-to-End Flow
1. HR defines quarter cycle (`year`, `quarter`, `start_date`, `deadline`).
2. API generates at least 5 follow-up reminder dates.
3. Feedback forms are submitted to API (`self`, `manager`, `client`, `peer`).
4. Server appends each submission to internal worksheet `feedback` (`record_type=submission`).
5. Server upserts summary metrics in internal `feedback` (`record_type=summary`).
6. Server mirrors summary output into HR-facing sheet (`Sheet1` by default) in the top table section.
7. Quarter summary endpoint reads submission rows for employee + quarter.
7. Score engine computes:
   - Per-form averages
   - Weighted appraisal score (`self 30% + avg(manager/client) 70%`)
   - Discrepancy alert (`self` vs `manager/client` gap > 1)
   - Positive/negative comment highlights
8. Yearly endpoint aggregates quarterly scores from workbook data.
9. HR email payload endpoints return quarterly/yearly report content.

## 3) Architecture Components
- API Layer (FastAPI): validation, routing, response contracts.
- Google Sheets Storage Adapter: append/read worksheet rows in pilot sheet.
- Rules Engine (`utils.py`): scoring, discrepancy, performance level, summaries.
- Notification Planner: follow-up schedule generation.
- Reporting Layer: quarter/year summaries + HR email payloads.

## 4) Logical Diagram
```text
Form Clients / Admin UI
        |
        v
      FastAPI
        |
        +--> Google Sheets Adapter ----> Parallel Pilot Google Sheet
        |
        +--> Scoring & Rules Engine ---> Quarter/Year Summary
        |
        +--> Alert Logic -------------> HR Discrepancy Payload
        |
        +--> Follow-up Planner -------> Reminder Dates
```

## 5) Current Implementation Mapping
- `server/main.py`
  - API endpoints
  - Excel-backed submission and reporting flows
- `server/google_sheets_store.py`
  - Worksheet checks
  - Submission append
  - Employee upsert
  - Quarter reads
- `server/utils.py`
  - Score and summary logic

## 6) Google Sheet Strategy
- Working model: separate pilot Google Sheet for development/alignment.
- Runtime controlled by env vars:
  - `GOOGLE_SHEETS_SPREADSHEET_ID`
  - `GOOGLE_SERVICE_ACCOUNT_FILE` or `GOOGLE_SERVICE_ACCOUNT_JSON`
- Recommended worksheets:
  - HR view: `Sheet1` (top table + KPI blocks)
  - Internal: `feedback`

## 7) App Script Integration (Optional Later)
Use Google Apps Script when you are ready to connect with HR's live Google Sheet.
- Option A: Keep direct API writes to pilot sheet and use Apps Script sync to HR sheet.
- Option B: Apps Script receives backend payloads and writes directly into HR-target format.

## 8) Delivery Phases
1. Google Sheet pilot persistence and schema alignment (now).
2. Add idempotency keys and duplicate protection.
3. Add scheduler + actual reminder email delivery.
4. Add Apps Script sync to HR master sheet.
5. Add test coverage for scoring and Google Sheets adapter.
