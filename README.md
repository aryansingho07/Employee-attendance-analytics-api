# Employee Attendance & Analytics API

A Python and FastAPI backend for employee punch-in, punch-out, manual attendance
corrections, and MongoDB attendance analytics. The implementation follows the
supplied HROne assignment contract, including overnight shifts, audit history,
concurrent writes, tied leaderboard ranks, and daily trends with missing-day rows.

**Stack:** Python 3.11+ · FastAPI · PyMongo · Pydantic · MongoDB 6.0+

**Verification:** The same 35 automated tests pass under Python 3.11.16 and
3.14.2 against MongoDB 7.0.16, including an independent comparison on 100,000
generated records. Full evidence, environment
details, and the limits of verification are recorded in [VERIFICATION.md](VERIFICATION.md).

The API has all 12 required HTTP operations. All application code is in
[`app/main.py`](app/main.py). The supplied [`openapi.yaml`](openapi.yaml) is the
contract; [`DATA_MODEL.md`](DATA_MODEL.md) defines the stored documents.

## Contents

- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [API reference](#api-reference)
- [A complete attendance workflow](#a-complete-attendance-workflow)
- [Business rules](#business-rules)
- [Manual corrections and audit history](#manual-corrections-and-audit-history)
- [Analytics implementation](#analytics-implementation)
- [Indexes and concurrent requests](#indexes-and-concurrent-requests)
- [Explain endpoint](#explain-endpoint)
- [Testing and verification](#testing-and-verification)
- [Troubleshooting](#troubleshooting)
- [Assignment deliverables and limits](#assignment-deliverables-and-limits)

## Quick start

### Prerequisites

- Python 3.11 or newer, available as `python` on Windows or `python3` on macOS/Linux.
- A running MongoDB 6.0+ instance, local or Atlas. Actual database verification
  used MongoDB 7.0.16; MongoDB 6.0 and Atlas were not exercised directly.
- A MongoDB account that can read/write the selected database and create indexes.
- Git, to clone the repository.

### Windows PowerShell

```powershell
git clone https://github.com/aryansingho07/Employee-attendance-analytics-api.git
cd Employee-attendance-analytics-api

python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:MONGO_URI = 'mongodb://127.0.0.1:27017'
$env:MONGO_DB = 'attendance_db'

uvicorn app.main:app --port 8000
```

Replace the URI with your MongoDB connection string when using a different
instance. The URI above is a local example with no credentials. MongoDB must
already be running; the API does not launch a database server.

If PowerShell prevents activation, use `.\.venv\Scripts\python.exe` for Python
commands and `.\.venv\Scripts\uvicorn.exe app.main:app --port 8000` to start the API.

### macOS or Linux

```bash
git clone https://github.com/aryansingho07/Employee-attendance-analytics-api.git
cd Employee-attendance-analytics-api

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export MONGO_URI='mongodb://127.0.0.1:27017'
export MONGO_DB='attendance_db'
uvicorn app.main:app --port 8000
```

The Windows startup was exercised during verification. These Unix shell commands
are provided for setup convenience; no macOS/Linux runtime was used in that audit.

### Check readiness

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

```json
{"status": "ok"}
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to try requests, or
[ReDoc](http://127.0.0.1:8000/redoc) to browse the generated documentation.
Use `openapi.yaml` for the assignment's full descriptions and response schemas.
Indexes are created automatically at startup, including when the database already
contains seeded documents. Existing attendance records are not rewritten.

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `MONGO_URI` | Yes | MongoDB connection URI. No connection URI is hard-coded in the application. |
| `MONGO_DB` | Yes | Database containing `employees` and `attendance_logs`. |
| `TEST_MONGO_URI` | For integration tests | MongoDB instance on which tests create disposable databases. |
| `RUN_SCALE_TESTS` | No | Set to `1` to enable the 100,000-record test. |

A local `.env` can supply the two application variables, but real environment
variables take priority because dotenv is loaded with `override=False`.
Environment files, credentials, virtual environments, caches, downloaded runtimes,
and local database files are excluded by `.gitignore`.

Application startup fails with a clear configuration error when either required
variable is missing. MongoDB must be reachable during index creation. Once the
application is running, `/health` returns 503 if the database stops answering.
The application makes no external service calls; its only network dependency is MongoDB.

## Project structure

```text
Employee-attendance-analytics-api/
├── app/
│   ├── __init__.py              # Empty package marker
│   └── main.py                  # All application code and pipeline builders
├── tests/
│   ├── __init__.py
│   ├── test_api.py              # Rules, response schemas, CRUD, races, analytics
│   ├── test_audit.py            # Additional assignment boundary checks
│   └── test_scale.py            # 100k data, independent expectations, explain, startup
├── openapi.yaml                 # Supplied HTTP contract
├── DATA_MODEL.md                # Supplied MongoDB data model
├── PLAN.md                      # Engineering plan and completion status
├── REVIEW.md                    # 30 starter findings with examples and fixes
├── DECISIONS.md                 # Five design answers within the requested word budget
├── VERIFICATION.md              # Requirement mapping, results, and unverified areas
├── requirements.txt             # Application dependencies
├── requirements-dev.txt         # Application and test dependencies
└── .gitignore
```

The candidate kit's Word document and JSON data dumps are not copied into this
repository. Tests generate their own data; they do not depend on files outside the clone.

## API reference

| Method | Endpoint | Main input | Success |
|---|---|---|---|
| GET | `/health` | None | 200 when MongoDB answers a ping |
| POST | `/employees` | Employee JSON | 201 |
| GET | `/employees` | Optional `department`, `page`, `page_size` | 200 |
| POST | `/attendance/punch-in` | `emp_code`, optional `punched_at`, `status` | 201 |
| POST | `/attendance/punch-out` | `emp_code`, optional `punched_at` | 200 |
| GET | `/attendance` | Optional `emp_code`, `date_from`, `date_to`, `status`, pagination | 200 |
| PATCH | `/attendance/{emp_code}/{date}` | Correction JSON | 200 |
| GET | `/analytics/employees/{emp_code}/monthly` | Required `month` | 200 |
| GET | `/analytics/departments/summary` | Required `month`, optional `department` | 200 |
| GET | `/analytics/leaderboard/late` | Required `month`, optional `department`, `limit` | 200 |
| GET | `/analytics/departments/{department}/trend` | Required `from`, `to` | 200 |
| GET | `/admin/explain/{endpoint}` | Named operation's query parameters | 200 |

### Identity, timestamps, and filtering

- `emp_code` is the employee's immutable business identifier, matching `EMP`
  followed by 4–6 digits when creating an employee. MongoDB `_id` is internal.
- Attendance is identified by `(emp_code, date)`. Resource responses have no `id` or `_id`.
- All instants are integer epoch **milliseconds**. Accepted request values are
  between `100000000000` and `4102444800000`, inclusive. Strings, floats, booleans,
  explicit null, and seconds-looking values are rejected with 422.
- Optional `punched_at` defaults to the server clock when omitted. Times are
  truncated to whole seconds before calculations and storage.
- Calendar dates are `YYYY-MM-DD`; months are `YYYY-MM`; shifts are `HH:MM` in IST.
  Impossible calendar dates are rejected, not just malformed strings.
- Department matches are exact and case-sensitive. Date filters are inclusive.
- Pagination defaults to `page=1`, `page_size=20`; page size must be 1–100.
  Employee pages sort by emp_code ascending. Attendance pages sort by date
  descending, then emp_code ascending. `total` counts the filtered set.
- Derived fields sent by clients are ignored. Clients cannot override calculated
  hours, late minutes, overtime, half-day flags, creation times, or audit history.

### Error responses

| Status | Meaning | Examples |
|---|---|---|
| 404 | Missing resource | Unknown employee, no eligible punch-in, no record to correct, unknown trend department |
| 409 | Conflict | Duplicate employee/day, already punched out, concurrent record change |
| 422 | Invalid input or final state | Bad timestamp/date/status, invalid pagination, no-op correction, invalid duration |
| 503 | MongoDB unavailable | Readiness ping or database operation fails |

Errors use `{"detail": ...}`. Validation errors contain a detail array with field
locations. MongoDB's raw error text and connection credentials are not exposed.

## A complete attendance workflow

Run these PowerShell examples after starting the API against an empty development
database. Reusing an existing employee code or attendance date returns 409.

### 1. Create an employee

```powershell
$base = 'http://127.0.0.1:8000'
$employee = @{
    emp_code = 'EMP0001'
    name = 'Asha Rao'
    email = 'asha@example.com'
    department = 'Engineering'
    joined_on = '2026-07-01'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$base/employees" `
    -ContentType 'application/json' -Body $employee
```

The default shift is 09:30–18:30 IST. Name length is 1–100, department length is
1–50, and email has the contract's format and maximum length of 120. Shift times
must differ. `created_at` is an integer timestamp set by the server.

### 2. Punch in

`1783311001000` is **6 July 2026, 09:40:01 IST**, just past the grace boundary.

```powershell
$punchIn = @{
    emp_code = 'EMP0001'
    punched_at = 1783311001000
    status = 'PRESENT'
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "$base/attendance/punch-in" `
    -ContentType 'application/json' -Body $punchIn
```

```json
{
  "emp_code": "EMP0001",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": 1783311001000,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 10,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 3. Punch out

`1783344600000` is **6 July 2026, 19:00:00 IST**.

```powershell
$punchOut = @{ emp_code = 'EMP0001'; punched_at = 1783344600000 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/attendance/punch-out" `
    -ContentType 'application/json' -Body $punchOut
```

The returned record has `work_hours: 9.33`, `overtime_minutes: 30`, and
`half_day: false`. Punch-out selects the most recent record whose punch-in is at
or before the supplied time, including a record from the previous calendar day.
If that selected record is already closed, the response is 409; it does not
silently close an older open record. A duration above 24 hours or at/below zero is invalid.

### 4. Correct the punch-in

```powershell
$correction = @{
    punch_in = 1783310400000
    reason = 'biometric correction'
    regularized_by = 'hr.admin'
} | ConvertTo-Json

Invoke-RestMethod -Method Patch -Uri "$base/attendance/EMP0001/2026-07-06" `
    -ContentType 'application/json' -Body $correction
```

The new punch-in is 09:30 IST. The returned record has late minutes 0 and hours
9.50, with one history entry containing the actual changes to punch-in,
late minutes, and work hours. JSON may display `9.5`; trailing decimal zeroes
are formatting, not a difference in the rounded numeric value.

### 5. Read lists and analytics

```powershell
Invoke-RestMethod "$base/employees?department=Engineering&page=1&page_size=20"
Invoke-RestMethod "$base/attendance?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31"
Invoke-RestMethod "$base/analytics/employees/EMP0001/monthly?month=2026-07"
Invoke-RestMethod "$base/analytics/departments/summary?month=2026-07"
Invoke-RestMethod "$base/analytics/leaderboard/late?month=2026-07&limit=10"
Invoke-RestMethod "$base/analytics/departments/Engineering/trend?from=2026-07-06&to=2026-07-08"
```

With only this record, July has 23 working days, one present day, and attendance
percentage 4.35. The department's average work hours is 9.50. After the correction,
the late leaderboard is empty. The three trend rows have rates 1.0, 0.0, and 0.0,
with moving averages 1.0, 0.5, and 0.3333.

## Business rules

| Rule | Implemented behavior |
|---|---|
| R1 — Time and date | Aware UTC BSON datetimes; IST attendance/shift calendars; whole-second truncation; overnight attribution and next-day shift end. |
| R2 — Late | Strictly more than 600 seconds after shift start; floor elapsed minutes from shift start, not the end of grace. |
| R3 — Overtime | Floor minutes after shift end; count only values at least 30. |
| R4 — Work hours | Elapsed seconds / 3600, Decimal half-up to two places; null until punch-out. |
| R5 — Half day | Rounded work hours below 4.50; weight 0.5 when counting presence. |
| R6 — Presence | PRESENT, WFH, ON_DUTY count; ABSENT and LEAVE do not. |
| R7 — Working days | Monday–Friday, from joined_on for monthly working-day denominators; weekends excluded from present_days but included in late/overtime totals. |
| R8 — Rounding | Two decimal places, half-up; daily rates and moving averages use four. |
| R9 — Headcount | Include employees who joined by the period's end/day, including employees without logs. |
| R10 — Pagination | Page starts at one; maximum page size 100; filtered total. |

For a 09:30 shift, 09:40:00 produces zero late minutes; 09:40:01 produces 10;
10:15:59 produces 45. A punch at 09:40:00.900 is truncated to 09:40:00 first.
For a 22:00–06:00 shift, a punch-in at 05:59:59 belongs to the previous shift date;
a punch-in at exactly 06:00:00 belongs to the current date.

| Status | Punch times | Presence weight |
|---|---|---|
| PRESENT, WFH, ON_DUTY | Punch-in required; punch-out can be pending | 1, or 0.5 for a half day |
| ABSENT, LEAVE | Both null | 0 |

The API does not create missing-day ABSENT records automatically. A missing
weekday and an explicit ABSENT record remain different stored situations.

## Manual corrections and audit history

PATCH accepts optional `status`, `punch_in`, and `punch_out`, plus required
`reason` (5–200 characters) and `regularized_by` (1–50 characters).
Omitted fields remain unchanged, subject to final-state rules:

- Setting ABSENT or LEAVE clears both punch times and resets derived values.
  Supplying either punch time with that status is invalid.
- A presence status needs a supplied or existing punch-in. The punch-in must
  resolve to the record's attendance date under the overnight rule.
- Punch-out must be strictly after punch-in and no more than 24 hours later.
- A correction that changes no stored value returns 422.

Every successful correction recalculates the derived fields and appends exactly
one history entry. It contains server time `at`, `by`, `reason`, and a `changes`
object with a before/after pair for each changed field. Nested punch timestamps
are BSON dates in MongoDB and epoch milliseconds in the API. Existing history is
preserved. Punch-in and punch-out do not append correction history.

## Analytics implementation

All four analytics are computed with MongoDB aggregation pipelines. Python
serializes the results; it does not fetch raw attendance documents and calculate
the analytics in loops. Stored derived values are authoritative for analytics.

| Endpoint | Pipeline behavior |
|---|---|
| Employee monthly | Indexed employee match; grouped monthly log lookup; database-generated working-day calendar; join-date denominator; half-day/weekday presence; leave/late/overtime totals and percentage. |
| Department summary | Start with employees joined by month end; preserve employees without logs; join grouped logs; sum counts; divide total eligible work hours by eligible record count. |
| Late leaderboard | Group monthly late totals; join employee metadata; discard orphan logs; filter department before ranking; use `$rank`; retain every rank at/below limit; order display ties by emp_code. |
| Department trend | Generate an inclusive calendar in MongoDB with `$range`/`$map`; fill missing days; count eligible daily headcount; use `$setWindowFields` for a seven-row average. |

Important distinctions:

- Department work hours are averaged **over eligible records**, never as an
  average of employee averages. Present-status records with null hours are excluded.
- Competition ranks can be `1, 2, 2, 4`. `limit=2` returns all rows ranked 1 or 2,
  even if that is more than two employees. Department filtering changes the ranking population.
- Trends include weekends. Present/late counts still reflect weekend records,
  but weekend attendance rates are null. A working day with headcount and no
  records has rate 0; a day without headcount has rate null.
- A trend range contains at most 92 days, inclusive. Its moving average ignores
  null rates and uses the current row plus up to six preceding rows within the
  requested range. A window with no non-null rate returns null.
- Missing seeded history/half_day become empty/false. Missing late/overtime
  fields are treated as zero. No seeded derived value is recomputed for analytics.

## Indexes and concurrent requests

All nine application indexes are created idempotently during startup.

| Collection | Index name | Keys | Purpose |
|---|---|---|---|
| employees | `employee_code` | emp_code ↑, unique | Identity, duplicate prevention, monthly match, joins |
| employees | `department_code` | department ↑, emp_code ↑ | Filtered employee list and ordering |
| employees | `department_joined` | department ↑, joined_on ↑ | Department eligibility and trend |
| employees | `joined_department` | joined_on ↑, department ↑ | Monthly eligibility across departments |
| attendance_logs | `employee_date` | emp_code ↑, date ↓, unique | One daily record; employee/month joins and lists |
| attendance_logs | `date_employee` | date ↓, emp_code ↑ | Date-ordered lists and monthly leaderboard scan |
| attendance_logs | `status_date_employee` | status ↑, date ↓, emp_code ↑ | Status-filtered global lists |
| attendance_logs | `employee_status_date` | emp_code ↑, status ↑, date ↓ | Employee/status-filtered lists |
| attendance_logs | `employee_punch` | emp_code ↑, punch_in ↓ | Most recent eligible punch-in |

Employee creation and punch-in insert directly into uniquely indexed collections.
Concurrent duplicates produce one 201 and 409 for the remaining requests.

Punch-out and corrections use atomic conditional updates. The update predicate
checks the values read earlier, including history and whether optional fields were
missing. A conflicting update returns 409 and can be retried after reading the
current record. A correction's `$push` appends history in the same atomic write
that updates the attendance values. No multi-document transaction is needed for
these single-record operations.

## Explain endpoint

Supported names are `attendance_list`, `employee_monthly`, `department_summary`,
`late_leaderboard`, and `department_trend`.

```powershell
Invoke-RestMethod "$base/admin/explain/attendance_list?emp_code=EMP0001&page=1&page_size=20"
Invoke-RestMethod "$base/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07"
Invoke-RestMethod "$base/admin/explain/department_summary?month=2026-07"
Invoke-RestMethod "$base/admin/explain/late_leaderboard?month=2026-07&limit=10"
Invoke-RestMethod "$base/admin/explain/department_trend?department=Engineering&from=2026-07-06&to=2026-07-08"
```

The response has `endpoint`, `collection`, and raw `explain` output produced with
`executionStats`. The operation builders are shared with the live endpoints,
including filters, sort, pagination, and index hints. Missing required parameters
return 422. Raw explain structures contain MongoDB query internals; the resource
response restriction on identifiers applies to employee/attendance documents.

Inspect IXSCAN/COLLSCAN, documents and keys examined, execution time, and foreign
lookup `collectionScans`. All five tested operations used indexes on the 100,000
record fixture, and foreign lookups reported zero collection scans. Explain BSON
metadata, including cluster timestamps and binary signatures, is JSON-serializable.

## Testing and verification

Install the development dependencies in your activated environment:

```powershell
python -m pip install -r requirements-dev.txt
$env:TEST_MONGO_URI = $env:MONGO_URI
python -m pytest -q

# Include the 100,000-record verification:
$env:RUN_SCALE_TESTS = '1'
python -m pytest -q -s
```

For a Unix shell, use `export TEST_MONGO_URI="$MONGO_URI"` and
`export RUN_SCALE_TESTS=1` before running the same pytest commands.

Without TEST_MONGO_URI, database-dependent tests skip. Without RUN_SCALE_TESTS=1,
the scale test skips. A skipped integration test is not evidence of a passing
database check. Tests create uniquely named `attendance_test_*` databases and
drop only those databases; the test account needs permission to create and drop them.

The verified suite covers strict input types and lengths, all response schemas,
time boundaries, half-up rounding, overnight shifts, record selection, corrections,
history, duplicate races, conflicting edits, filtered pagination, joiners, leap
months, employees without logs, orphan logs, weekend totals, cutoff ties, gap
filling, moving averages, environment priority, and all five explain plans.

The scale test seeds 1,000 employees and 100,000 logs before index creation,
compares every analytics endpoint with independent expected calculations, checks
the plans, and launches a real Uvicorn process. [VERIFICATION.md](VERIFICATION.md)
also records the clean-install live HTTP check and a requirement-by-requirement matrix.
Local timing results are observations, not latency guarantees or a promise about the hidden grader.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Missing MONGO_URI/MONGO_DB error | Set both variables in the same terminal that launches Uvicorn, or use a local ignored `.env`. |
| MongoDB unavailable / startup fails | Ensure the server is running, the URI/port is correct, and credentials, Atlas network access, and index permissions are configured. |
| Port 8000 is occupied | Stop the process using that port. Use port 8000 for the assignment's required command. |
| Duplicate employee/day returns 409 | Expected behavior; use a different business code or date, or correct the existing record. |
| PATCH returns 422 | Check timestamp types, reason/by lengths, attendance-date attribution, final status, duration, and whether any value actually changes. |
| PATCH returns 409 | Another request changed the record; read its current values before retrying. |
| Integration tests skip | Set TEST_MONGO_URI; set RUN_SCALE_TESTS=1 to include the scale test. |
| Index creation rejects duplicate keys | Check pre-existing data for duplicate emp_code or employee/date pairs. The supplied data model requires uniqueness. |

## Assignment deliverables and limits

| Part | Deliverable |
|---|---|
| A — Review | [REVIEW.md](REVIEW.md): 30 concrete starter defects, reproduction examples, fixes, and examined non-defects. |
| B — Build | [app/main.py](app/main.py): all 12 operations, business rules, atomic writes, and MongoDB analytics. |
| C — Prove and explain | Five explain modes and [DECISIONS.md](DECISIONS.md), currently 199 words including its heading/labels. |
| Run and verify | This README, dependency files, tests, [PLAN.md](PLAN.md), and [VERIFICATION.md](VERIFICATION.md). |

This is an assignment backend. Authentication, holiday calendars, shift management,
automatic absence generation, deployment, and a frontend are outside its specified
scope. No application endpoints are intentionally missing. The hiring team's
hidden data, live walkthrough, and evaluation have not been run, so this repository
does not claim guaranteed grader acceptance.

Repository: [aryansingho07/Employee-attendance-analytics-api](https://github.com/aryansingho07/Employee-attendance-analytics-api).
Submitting that URL to the hiring team through their requested channel remains a
separate human step; no submission email or form has been sent.
