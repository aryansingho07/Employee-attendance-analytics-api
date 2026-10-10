# Employee Attendance & Analytics API

Complete FastAPI implementation of the supplied contract. Python 3.11+ and
MongoDB 6.0+ are required; verification used MongoDB 7.0.16. All application
code is in `app/main.py`; no authentication is required by this assignment.

From the repository root, install and start (PowerShell):

```powershell
python -m pip install -r requirements.txt
$env:MONGO_URI = 'mongodb://127.0.0.1:27017'
$env:MONGO_DB = 'attendance_db'
uvicorn app.main:app --port 8000
```

Use your local MongoDB or Atlas URI. Both environment variables are required;
a local `.env` may provide them, and actual environment variables take priority.
Indexes are created automatically at startup without changing seeded documents.
`GET /health` pings MongoDB; interactive API documentation is at `/docs`.
Requests and responses use integer epoch milliseconds for instants; attendance
dates and shifts use IST. The supplied `openapi.yaml` remains the contract.

Run tests against a disposable database on your MongoDB instance:

```powershell
python -m pip install -r requirements-dev.txt
$env:TEST_MONGO_URI = $env:MONGO_URI
python -m pytest -q
$env:RUN_SCALE_TESTS = '1'
python -m pytest -q -s tests/test_scale.py
```

Tests create uniquely named `attendance_test_*` databases and remove only those
databases. Without TEST_MONGO_URI, integration tests skip; helper tests still run.
The scale test generates 100,000 records, checks analytics against independent
expectations, checks all five explain plans, and starts an actual Uvicorn process.

See `PLAN.md`, `REVIEW.md`, `DECISIONS.md`, and `VERIFICATION.md` for the engineering
plan, starter fixes, design answers, and observed results. No endpoints are missing.
Local downloaded runtimes, dependencies, caches, and database files are ignored;
do not include them, secrets, `.env`, or a Dockerfile in the public submission.
Repository: https://github.com/Aryansingh95203/Employee-attendance-analytics-api
Submission of the repository URL to the hiring team is a separate step.
