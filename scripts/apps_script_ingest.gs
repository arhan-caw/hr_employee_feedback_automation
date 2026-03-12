/**
 * Apps Script template for detached ingestion.
 *
 * Setup:
 * 1) Bind this script to your Google Form response spreadsheet.
 * 2) Update API_BASE_URL and API_KEY (if you add auth).
 * 3) Create installable trigger: onFormSubmit (from spreadsheet).
 * 4) Create time-driven trigger: processQueue (e.g. every 5 minutes).
 */

const API_BASE_URL = "https://YOUR_RENDER_URL";
const API_KEY = ""; // optional if you add API auth later

function onFormSubmit(e) {
  try {
    const named = e.namedValues || {};
    // Map these keys to your actual form question titles.
    const employeeEmail = firstValue(named["Employee Email"]);
    const employeeName = firstValue(named["Employee Name"]);
    const managerEmail = firstValue(named["Manager Email"]);
    const clientEmail = firstValue(named["Client Email"]);
    const peer1 = firstValue(named["Peer 1"]);
    const peer2 = firstValue(named["Peer 2"]);
    const quarter = firstValue(named["Quarter"]);
    const year = Number(firstValue(named["Year"]));
    const formType = firstValue(named["Form Type"]).toLowerCase();
    const answers = parseAnswers(named["Answers"]);
    const comments = parseComments(named["Comments"]);

    const timestamp = e.values && e.values.length ? e.values[0] : new Date().toISOString();
    const eventId = buildEventId(employeeEmail, formType, year, quarter, timestamp);

    const payload = {
      event_id: eventId,
      employee_email: employeeEmail,
      employee_name: employeeName,
      manager_email: managerEmail,
      client_email: clientEmail,
      peer_1_name: peer1,
      peer_2_name: peer2,
      quarter: quarter,
      year: year,
      form_type: formType,
      answers: answers,
      comments: comments
    };

    postJson(API_BASE_URL + "/feedback", payload);
  } catch (err) {
    // Keep visible in Apps Script logs for debugging.
    Logger.log("onFormSubmit error: " + err);
  }
}

function processQueue() {
  const payload = { limit: 50, max_attempts: 10 };
  postJson(API_BASE_URL + "/queue/process", payload);
}

function postJson(url, payload) {
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["x-api-key"] = API_KEY;

  return UrlFetchApp.fetch(url, {
    method: "post",
    headers: headers,
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
}

function firstValue(v) {
  if (!v) return "";
  if (Array.isArray(v)) return (v[0] || "").toString().trim();
  return v.toString().trim();
}

function parseAnswers(raw) {
  const text = firstValue(raw);
  if (!text) return [];
  return text
    .split(/[,\s]+/)
    .map(function (x) { return Number(x); })
    .filter(function (n) { return !isNaN(n); });
}

function parseComments(raw) {
  const text = firstValue(raw);
  if (!text) return [];
  return text.split("|").map(function (x) { return x.trim(); }).filter(function (x) { return x; });
}

function buildEventId(email, formType, year, quarter, timestamp) {
  const key = [email || "unknown", formType || "unknown", year || "0", quarter || "NA", timestamp || ""].join("|");
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, key);
  return digest.map(function (b) {
    const v = (b < 0 ? b + 256 : b).toString(16);
    return v.length === 1 ? "0" + v : v;
  }).join("").slice(0, 32);
}
