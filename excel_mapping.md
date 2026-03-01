# HR View + Internal Mapping

## Purpose
Keep HR-style single table view in `Sheet1`, while preserving reliable raw ingestion/calculation in an internal `feedback` worksheet.

## Worksheets
- `Sheet1` (or `HR_SUMMARY_SHEET`): HR-facing top table plus existing KPI blocks below.
- `feedback`: internal rows used by API for raw submissions and summary snapshots.

## `feedback` Columns
1. `record_type` (`submission` | `summary`)
2. `employee_id` (email-first identity key)
3. `employee_name`
4. `year`
5. `quarter`
6. `form_type`
7. `answers_json`
8. `comments_json`
9. `submitted_at`
10. `manager_email`
11. `client_email`
12. `peer_1`
13. `peer_2`
14. `self_avg`
15. `client_avg`
16. `peer_avg`
17. `manager_avg`
18. `total_average`
19. `client_diff_gt_1`
20. `manager_diff_gt_1`
21. `client_diff_gt_05`
22. `manager_diff_gt_05`

## Behavior
- Every form submit appends one `submission` row.
- System also upserts one internal `summary` row per `employee_id + year + quarter`.
- System mirrors computed summary into `Sheet1` top table and inserts above KPI rows.

## Identity
- Preferred key: `employee_email` (normalized lowercase, stored in `employee_id` column).
- Fallback: `employee_id` when email is not provided.
