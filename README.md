# HR Employee Feedback Automation

## Requirements

### Form Types
The system will collect feedback through four distinct forms:
- **Employee Form** (Self-feedback)
- **Manager Form**
- **Client Form**
- **Peer Form** (feedback only gathered; not used in appraisals)

### Feedback Collection Process
- Feedback collection will have a **deadline**
- **HR team follow-ups**: At least 5 regular email follow-ups within the deadline period to encourage submission

### Data Processing & Scoring
- All form responses are collected into a **final data sheet**
- **Average score calculation**: For each form, calculate the average of all answers
- **Score Weightage**:
  - Self-feedback: 30%
  - Manager + Client combined: 70%
  - Peer feedback: Not used in appraisals

### Quarterly & Yearly Analysis
- Above scoring methodology applies to each **quarter**
- **Yearly sheet data** will be used for generating **appraisal emails**
- An algorithm will be developed to calculate yearly performance metrics from quarterly data

### Performance Identification (Server-Side)
Based on the feedback rating, the server will:
- Identify the employee's **performance level**
- Highlight **areas of improvement**
- Highlight **what the employee is already doing well**
- Generate **quarterly and yearly HR email payloads** for reporting

### Discrepancy Alerts
- If there is a **rating difference > 1** between self-feedback and Manager/Client feedback, an **alert email** will be sent to the HR team

### Individual Employee Tracking
- Each employee will have an **individual sheet** tracking quarterly data
- The sheet will display both **positive and negative feedback summaries** for performance tracking

## Google Sheet Pilot Setup

This backend writes to a **parallel pilot Google Sheet** (not HR's existing sheet).

### Environment variables
- `STORAGE_BACKEND`: `local_excel` (testing default) or `google_sheets`
- `GOOGLE_SHEETS_SPREADSHEET_ID`: target pilot Google Sheet ID
- `GOOGLE_SERVICE_ACCOUNT_FILE`: path to service-account JSON file
  - Alternative: `GOOGLE_SERVICE_ACCOUNT_JSON` with raw JSON content
- `FEEDBACK_WORKBOOK_PATH`: local file path when `STORAGE_BACKEND=local_excel`
- `HR_SUMMARY_SHEET`: HR-facing worksheet name (default: `Sheet1`)

### Required sheet access
Share the pilot Google Sheet with the service account email as **Editor**.

### Expected worksheets
- HR view sheet: `Sheet1` (or configured `HR_SUMMARY_SHEET`)
- Internal sheet: `feedback`

See `excel_mapping.md` for exact columns and payload mapping.

## Identity Key
- Preferred key: `employee_email` (normalized to lowercase)
- Backward-compatible fallback: `employee_id`
- Existing routes keep `/employees/{employee_id}/...` for compatibility, but you can pass email in that path (for example `/employees/alex@caw.tech/quarters/2026/Q1`).

### Quick start for weekend testing (no GCP billing needed)
- Set `STORAGE_BACKEND=local_excel`
- Run API and test flows against local file (default: `server/data/feedback_store.xlsx`)
- On Monday, switch to `STORAGE_BACKEND=google_sheets` and set Google env vars

## Run Commands (PowerShell)

### 1) Install dependencies
```powershell
cd server
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) Create env file
```powershell
Copy-Item .env.example .env
```

### 3) Local testing mode (weekend)
Edit `.env` to keep:
```env
STORAGE_BACKEND=local_excel
FEEDBACK_WORKBOOK_PATH=server/data/feedback_store.xlsx
```

Run API:
```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 4) Switch to Google Sheets mode (Monday)
Edit `.env`:
```env
STORAGE_BACKEND=google_sheets
GOOGLE_SHEETS_SPREADSHEET_ID=your_sheet_id
GOOGLE_SERVICE_ACCOUNT_FILE=C:\path\to\service-account.json
```

Run API again:
```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Deploy (Phase 1)

### Recommended platform
- Use **Render** for this FastAPI backend.
- Vercel is possible but less suitable for this workflow (Python serverless constraints, no long-running process model).

### Render setup (using `render.yaml`)
1. Push this repo to GitHub.
2. In Render: **New +** -> **Blueprint** -> select this repo.
3. Render reads [render.yaml](./render.yaml) and creates service `hr-feedback-api`.
4. Set environment variables in Render service:
   - `STORAGE_BACKEND=google_sheets`
   - `HR_SUMMARY_SHEET=Sheet1`
   - `GOOGLE_SHEETS_SPREADSHEET_ID=<your_pilot_sheet_id>`
   - `GOOGLE_SERVICE_ACCOUNT_JSON=<full_json_string>`
5. Deploy.
6. Verify health endpoint:
   - `https://<your-render-url>/health`

### Phase 1 test checklist
1. Submit `self` form for a new email -> response should show `summary_status=pending_required_forms`.
2. Submit `manager` or `client` for same user -> `summary_status=updated`.
3. Open pilot Google Sheet:
   - internal sheet `feedback` has submission and summary rows
   - `Sheet1` top table has/upserts employee summary
4. Check quarterly endpoint:
   - `/employees/<email>/quarters/<year>/<quarter>`
5. Check discrepancy alert endpoint:
   - `/employees/<email>/alerts/<year>/<quarter>`
