# Assignment verification

Audit date: **11 October 2026, Asia/Kolkata**. The original Word assignment,
OpenAPI descriptions/schemas, data model, starter review, and current code were
read again. No missing application operation or observed contract mismatch was
found. No application-code change was needed; test coverage and documentation
were expanded. Passing these checks does not guarantee hidden-grader acceptance.

The expanded suite ran on Windows with Python 3.14.2, FastAPI 0.141.1, Pydantic
2.13.4, PyMongo 4.18.3, Uvicorn 0.52.3, and real MongoDB 7.0.16. No mock database
was used for aggregation semantics or query plans. A portable MongoDB runtime
was used because Docker was unavailable; it is ignored and is not a deliverable.

The same full suite was also run using the clean Python 3.11.16 environment with
the newer dependencies listed below: **35 passed in 10.92 seconds**. That run
reported one third-party Starlette TestClient deprecation warning about its httpx
integration; no test failed. The Python 3.14.2 run passed in 10.76 seconds.

## Clean installation and live API

A fresh GitHub clone and a new **Python 3.11.16** virtual environment successfully
installed only `requirements.txt` before the live API check. This resolved FastAPI
0.143.0, Pydantic 2.14.0, PyMongo 4.18.3, and Uvicorn 0.54.0. The cloned app file's
hash matched the working project's app file.

The exact command `uvicorn app.main:app --port 8000`, run from the clean clone's
root, became ready in **1.709 seconds**. Live HTTP requests exercised all 12
assignment operations, all five explain modes, the README's attendance workflow,
404/409/422 responses, and Swagger UI. A real MongoDB shutdown after startup
proved that `/health` returns **503 over HTTP** when the database stops answering.
The disposable live-test database was removed before shutdown.

## Tests

- 34 focused API/helper/audit tests and one 100,000-record scale test passed together
  under both Python 3.14.2 and Python 3.11.16,
  including validation of successful responses against the supplied OpenAPI
  schemas and recursive checks for zero foreign lookup collection scans.
- A focused serialization test verified replica-set/Atlas BSON cluster timestamps
  and binary signatures in explain responses. An actual Atlas connection was not used.
- Eight concurrent identical employee creates returned one 201 and seven 409s.
  Eight concurrent identical punch-ins did the same. Eight punch-outs returned
  one 200 and seven 409s. Two synchronized corrections returned 200/409 and kept
  exactly the winning audit entry.
- Verified grace at 09:40:00 and 09:40:01, second truncation, half-up midpoint
  rounding, the rounded half-day threshold, overtime at 29:59/30:00, overnight
  attendance dates, and invalid/24-hour duration boundaries.
- Verified joiners, future employees, employees without logs, missing seeded
  fields, absent versus missing records, orphan logs, weekend totals, weighted
  record averages, competition ranks and cutoff ties, empty ranges, changing
  daily headcount, and shorter initial moving-average windows.
- Added checks for all contract routes, the single-file application structure,
  the DECISIONS word budget, latest eligible punch-out selection, exact 24-hour
  duration, overnight corrections, ABSENT/ON_DUTY restoration, leap months,
  weekend-only windows, 92-day ranges, input-length/timestamp bounds, and actual
  environment-variable priority over a synthetic `.env` in a subprocess.

## Scale check

The test generated 1,000 employees in ten departments and 100,000 attendance
records, initially with only MongoDB's default indexes. Monthly, department,
leaderboard, and trend outputs matched independent expected calculations.

Startup including all index creation took **1.826 seconds**. An actual Uvicorn
process became ready in **1.047 seconds** against the indexed database.

| Explained operation | Main cursor keys examined | Main cursor documents examined | executionTimeMillis |
|---|---:|---:|---:|
| Attendance list, page 10 of 20 | 200 | 20 | 0 |
| Employee monthly | 1 | 1 | 1 |
| Department summary | 1,000 | 1,000 | 601 |
| Late leaderboard | 31,000 | 31,000 | 199 |
| Department trend | 100 | 100 | 48 |

Every explained operation contained IXSCAN and no COLLSCAN. Cross-collection
lookups also reported no collection scans. Main-cursor counts above do not
include foreign lookup documents; the raw explain endpoint preserves those
statistics. Timings are observations from one local run, not service guarantees.

## Reproduce

Install `requirements-dev.txt`, set TEST_MONGO_URI, and run
`python -m pytest -q`. Set RUN_SCALE_TESTS=1 to include the generated scale test.
Only uniquely named databases created by the tests are dropped during cleanup.
The generated database files, downloaded runtime, dependencies, and caches are
ignored and are not submission artifacts.

The one-off clean-install live check is separate from the 35-test count. The
published automated tests generate their own data and need no candidate-kit files.
Missing TEST_MONGO_URI or RUN_SCALE_TESTS can cause skips; a skip is not a pass.

## Requirement-by-requirement evidence

| Requirement | Evidence | Result |
|---|---|---|
| Python 3.11+, FastAPI, PyMongo, MongoDB 6.0+ | Clean install/live service on 3.11.16; full suite on 3.11.16 and 3.14.2; MongoDB 7.0.16 | Passed on these versions |
| Application code only in app/main.py | `test_all_assignment_operations_exist_and_app_is_single_file`; empty package marker; source review | Passed |
| All 12 HTTP operations | Contract route enumeration, response schemas, live HTTP | Passed |
| Root dependencies and review/design/run documents | Tracked-file inspection; clean install; document review | Present |
| Required command ready within 20 seconds | Exact port-8000 command in clean clone; 100k startup test | Passed |
| Real environment variables beat .env | `test_dotenv_cannot_override_real_environment`; source inspection | Passed |
| Startup indexes are idempotent | Seed before startup; inspect indexes; repeated create_indexes | Nine application indexes, repeat succeeds |
| Readiness reflects the database | Focused failure test and real MongoDB shutdown during live HTTP | 200 online, 503 offline |
| Business identifiers and time types | Projections, response schemas, recursive timestamp conversion, stored audit inspection | No id/_id on resources; UTC BSON dates and integer milliseconds |
| R1: IST/overnight/whole-second truncation | Overnight attribution and correction tests; duplicate-punch fixture | Passed, including exact shift-end boundary |
| R2: grace and late floors | `test_late_boundaries`; live 09:40:01 punch | 09:40:00 → 0; 09:40:01 → 10; 10:15:59 → 45 |
| R3: overtime minimum/floor | Boundary and overnight tests; live punch-out | Below 30 → 0; 30:00 → 30 |
| R4: half-up hours and pending null | `test_half_up_and_half_day`; live workflow | Midpoint 4.505 → 4.51 |
| R5: half day from rounded hours | Parameterized tests and corrected overnight record | 4.495 → 4.50, not half day |
| R6: presence statuses | Status validation, mixed fixtures, independent scale expectations | PRESENT/WFH/ON_DUTY counted; ABSENT/LEAVE excluded |
| R7: weekdays and joined_on | Joiner/weekend tests; leap-February test; scale comparison | Correct denominator and weekend totals |
| R8: reported half-up rounding | Average midpoint 6.525 → 6.53; monthly percentage; independent trend comparison | Passed |
| R9: zero-log and future headcounts | Summary/trend fixtures; empty/future department checks | Eligible zero-log staff retained; future staff excluded until eligible |
| R10: pagination and totals | List tests, database pagination, explain skip/limit check | Ordering, bounds, defaults, filtered totals passed |
| Employee validation and duplicate race | Patterns/lengths/shift checks; eight parallel creates | One 201/seven 409; invalid input 422 |
| Punch-in initial values and duplicate race | Eight simultaneous punches; schema/live checks | One 201/seven 409; correct defaults |
| Punch-out selection/duration | Closed latest versus older open; future-only; exact 24 hours; overnight | Correct 404/409/422 behavior; exactly 24 hours allowed |
| Punch-out concurrent closure | Eight parallel punch-outs | One 200/seven 409; no correction history appended |
| Correction final-state rules and audit | No-op/date/duration/status tests, clear/restore transitions, nested BSON changes | Recomputed values and one append per success |
| Concurrent edits preserve history | Synchronized race in `test_simultaneous_corrections_keep_history` | 200/409, winning entry retained |
| Monthly aggregation | Source review, no-log/joiner/leap fixtures, independent scale comparison | Passed |
| Department cross-collection aggregation | Zero-log headcount, record-weighted averages, independent scale comparison | Passed |
| Competition leaderboard/cutoff | $rank; tie/skipped-rank/department/orphan fixtures and scale comparison | Passed |
| Database gap filling/window average | $range/$map and $setWindowFields source; gap/weekend/headcount/window/92-day tests | Passed; no Python attendance-document calculation loop |
| Explain uses the real operation | Shared builders; filters/sort/page checks; all five modes at scale | executionStats, IXSCAN, no COLLSCAN, foreign scans zero |
| Expected status/body fields | Response-schema tests and live 404/409/422/503 cases | Passed; no 500 observed |
| Application network dependency only MongoDB | Source/import review | No external service calls in application code |
| REVIEW defects plus examined non-defects | 30 entries with original locations, examples, fixes, and retained behavior notes | Present and reviewed |
| DECISIONS five answers and word budget | Five answers; automated word-budget check | 199 words including heading/labels |
| No forbidden committed artifacts | Tracked paths and .gitignore checks | No environment files, secrets, Dockerfile, venv, binaries, or JSON data dump tracked |
| Public repository | Fresh clone from aryansingho07/Employee-attendance-analytics-api | Accessible |
| Submission deadline/email and live walkthrough | Not performed in this audit | Remain external human steps |

## Limits

- The hidden dataset and grader are unavailable; these results cannot certify
  every possible input, grading score, or acceptance.
- MongoDB 6.0 and an actual Atlas connection were not run. Cluster BSON metadata
  serialization has a focused synthetic test, separate from the real database tests.
- This audit ran on Windows. It did not test macOS/Linux execution, sustained
  production traffic, partitions, multi-host deployment, or ten million records.
- Starter findings were checked against the supplied code and behavior, not the
  hiring team's unpublished list of known defects.
- Submitting the URL through the hiring team's channel by its deadline and taking
  the live walkthrough remain the candidate's responsibility. Neither was done here.

## Implementation references

The aggregation uses explicit Decimal half-up arithmetic because MongoDB's
[$round rounds ties to even](https://www.mongodb.com/docs/manual/reference/operator/aggregation/round/).
The leaderboard uses MongoDB's
[$rank competition ranking](https://www.mongodb.com/docs/v5.3/reference/operator/aggregation/rank/).
The trend generates its bounded calendar with $range/$map in MongoDB and uses
[$setWindowFields](https://www.mongodb.com/docs/v7.0/reference/operator/aggregation/setwindowfields/)
for its seven-row average.

The hiring team's hidden dataset has not been run. Submission of the GitHub
repository URL to the hiring team is a separate step.
