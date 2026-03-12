/**
 * Reminder sender (Phase 1): Gmail + Apps Script
 *
 * What it does:
 * 1) Fetch pending reminder candidates from backend /reminders/candidates.
 * 2) Send reminder emails via GmailApp.
 * 3) Write reminder audit rows to reminder_log sheet.
 *
 * Setup:
 * - Bind this script to a Google Sheet (any ops/admin sheet is fine).
 * - Set API_BASE_URL (Render URL).
 * - Optionally set API_KEY if API auth is enabled.
 * - Create trigger for sendPendingReminders (time-driven or manual).
 */

const API_BASE_URL = "https://YOUR_RENDER_URL";
const API_KEY = "";
const TARGET_YEAR = 2026;
const TARGET_QUARTER = "Q1";
const REQUIRED_FORMS = ["self"]; // ex: ["self","manager","client","peer"]

function sendPendingReminders() {
  const payload = {
    year: TARGET_YEAR,
    quarter: TARGET_QUARTER,
    required_forms: REQUIRED_FORMS,
    include_completed: false
  };
  const data = postJson(API_BASE_URL + "/reminders/candidates", payload);
  const candidates = (data && data.candidates) || [];

  const logSheet = getOrCreateReminderLogSheet_();
  let sent = 0;
  let skipped = 0;

  for (var i = 0; i < candidates.length; i++) {
    const row = candidates[i];
    const toEmail = (row.employee_email || "").trim();
    if (!toEmail) {
      skipped++;
      continue;
    }

    const missingForms = row.missing_forms || [];
    if (!missingForms.length) {
      skipped++;
      continue;
    }

    const fingerprint = [TARGET_YEAR, TARGET_QUARTER, toEmail.toLowerCase(), missingForms.join(",")].join("|");
    if (wasReminderSentToday_(logSheet, fingerprint)) {
      skipped++;
      continue;
    }

    const name = row.employee_name || "Team Member";
    const subject = "[Reminder] Pending feedback forms - " + TARGET_QUARTER + " " + TARGET_YEAR;
    const body =
      "Hi " + name + ",\n\n" +
      "This is a reminder that the following feedback form(s) are still pending for " +
      TARGET_QUARTER + " " + TARGET_YEAR + ":\n" +
      "- " + missingForms.join("\n- ") + "\n\n" +
      "Please complete them as soon as possible.\n\n" +
      "Thanks,\nHR Team";

    GmailApp.sendEmail(toEmail, subject, body);
    logSheet.appendRow([
      new Date().toISOString(),
      TARGET_YEAR,
      TARGET_QUARTER,
      toEmail,
      name,
      missingForms.join(","),
      "sent",
      fingerprint
    ]);
    sent++;
  }

  Logger.log("Reminder run completed. sent=" + sent + " skipped=" + skipped + " candidates=" + candidates.length);
}

function postJson(url, payload) {
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["x-api-key"] = API_KEY;

  const res = UrlFetchApp.fetch(url, {
    method: "post",
    headers: headers,
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  const code = res.getResponseCode();
  const text = res.getContentText();
  Logger.log("POST " + url + " code=" + code);
  Logger.log("resp=" + text);

  if (code < 200 || code >= 300) {
    throw new Error("API call failed: " + code + " " + text);
  }
  return JSON.parse(text || "{}");
}

function getOrCreateReminderLogSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const name = "reminder_log";
  let ws = ss.getSheetByName(name);
  if (!ws) {
    ws = ss.insertSheet(name);
    ws.appendRow(["timestamp", "year", "quarter", "employee_email", "employee_name", "missing_forms", "status", "fingerprint"]);
  } else if (ws.getLastRow() < 1) {
    ws.appendRow(["timestamp", "year", "quarter", "employee_email", "employee_name", "missing_forms", "status", "fingerprint"]);
  }
  return ws;
}

function wasReminderSentToday_(ws, fingerprint) {
  const lastRow = ws.getLastRow();
  if (lastRow < 2) return false;
  const today = new Date();
  const tz = Session.getScriptTimeZone();
  const todayKey = Utilities.formatDate(today, tz, "yyyy-MM-dd");
  const values = ws.getRange(2, 1, lastRow - 1, 8).getValues();

  for (var i = values.length - 1; i >= 0; i--) {
    const ts = values[i][0];
    const fp = (values[i][7] || "").toString();
    if (fp !== fingerprint) continue;
    const sentDate = new Date(ts);
    const sentKey = Utilities.formatDate(sentDate, tz, "yyyy-MM-dd");
    if (sentKey === todayKey) return true;
  }
  return false;
}
