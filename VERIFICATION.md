# Verification results

Verified on Windows with Python 3.14.2, PyMongo 4.18.3, and a real MongoDB
7.0.16 instance bound to localhost. The application uses Python 3.11-compatible
syntax. Docker was unavailable, so an ignored portable MongoDB runtime was used.
No mock database was used to validate aggregation semantics or query plans.

## Tests

- 27 focused API/helper tests and one 100,000-record scale test passed together
  (**28 passed in 6.11 seconds**),
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

## Scale check

The test generated 1,000 employees in ten departments and 100,000 attendance
records, initially with only MongoDB's default indexes. Monthly, department,
leaderboard, and trend outputs matched independent expected calculations.

Startup including all index creation took **1.812 seconds**. An actual Uvicorn
process became ready in **1.132 seconds** against the indexed database.

| Explained operation | Main cursor keys examined | Main cursor documents examined | executionTimeMillis |
|---|---:|---:|---:|
| Attendance list, page 10 of 20 | 200 | 20 | 0 |
| Employee monthly | 1 | 1 | 1 |
| Department summary | 1,000 | 1,000 | 616 |
| Late leaderboard | 31,000 | 31,000 | 216 |
| Department trend | 100 | 100 | 25 |

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
