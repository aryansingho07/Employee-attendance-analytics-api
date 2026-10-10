"""Additional assignment audit: contract boundaries and ambiguous happy paths."""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app import main
from tests.test_api import api, employee, log, ms


def test_all_assignment_operations_exist_and_app_is_single_file():
    root = Path(__file__).parents[1]
    contract = yaml.safe_load((root / "openapi.yaml").read_text())
    expected = {(method.upper(), path) for path, item in contract["paths"].items()
                for method in item if method in {"get", "post", "patch", "put", "delete"}}
    actual = {(method, route.path) for route in main.app.routes
              for method in getattr(route, "methods", [])}
    assert expected <= actual
    assert len(expected) == 12
    assert sorted(path.name for path in (root / "app").glob("*.py")) == ["__init__.py", "main.py"]
    assert not (root / "app" / "__init__.py").read_text().strip()
    ast.parse((root / "app" / "main.py").read_text(), feature_version=(3, 11))
    decisions = (root / "DECISIONS.md").read_text().split()
    assert 150 <= len(decisions) <= 200


def test_punch_out_selects_latest_eligible_even_when_closed(api):
    http, database = api
    database.employees.insert_one(employee())
    database.attendance_logs.insert_many([
        log("EMP0001", "2026-07-06", hours=None),
        log("EMP0001", "2026-07-07", hours=9),
        log("EMP0001", "2026-07-08", hours=None),
    ])
    # Do not fall back to the older open record when the latest eligible one is closed.
    response = http.post("/attendance/punch-out", json={
        "emp_code": "EMP0001", "punched_at": ms("2026-07-07", "19:00:00"),
    })
    assert response.status_code == 409, response.text
    assert database.attendance_logs.find_one({"date": "2026-07-06"})["punch_out"] is None
    response = http.post("/attendance/punch-out", json={
        "emp_code": "EMP0001", "punched_at": ms("2026-07-08", "18:30:00"),
    })
    assert response.status_code == 200 and response.json()["date"] == "2026-07-08"
    # Just under 24 hours is permitted, and a future punch-in is not eligible.
    response = http.post("/attendance/punch-out", json={
        "emp_code": "EMP0001", "punched_at": ms("2026-07-07", "09:29:59"),
    })
    assert response.status_code == 200 and response.json()["date"] == "2026-07-06"
    assert response.json()["work_hours"] == 24.0


def test_exact_24_hours_and_future_only_punch(api):
    http, database = api
    database.employees.insert_one(employee())
    database.attendance_logs.insert_one(log("EMP0001", "2026-07-06", hours=None))
    assert http.post("/attendance/punch-out", json={
        "emp_code": "EMP0001", "punched_at": ms("2026-07-06", "09:29:59"),
    }).status_code == 404
    result = http.post("/attendance/punch-out", json={
        "emp_code": "EMP0001", "punched_at": ms("2026-07-07", "09:30:00"),
    })
    assert result.status_code == 200 and result.json()["work_hours"] == 24.0


def test_overnight_regularization_and_recomputed_audit(api):
    http, database = api
    database.employees.insert_one(employee(shift_start="22:00", shift_end="06:00"))
    start = ms("2026-07-06", "22:00:00")
    http.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": start})
    path = "/attendance/EMP0001/2026-07-06"
    metadata = {"reason": "correct biometric times", "regularized_by": "hr.admin"}
    result = http.patch(path, json={
        **metadata, "punch_in": ms("2026-07-07", "01:00:00"),
        "punch_out": ms("2026-07-07", "06:30:00"),
    })
    assert result.status_code == 200, result.text
    record = result.json()
    assert (record["date"], record["late_minutes"], record["work_hours"], record["overtime_minutes"], record["half_day"]) == (
        "2026-07-06", 180, 5.5, 30, False,
    )
    assert set(record["history"][0]["changes"]) == {"punch_in", "punch_out", "late_minutes", "work_hours", "overtime_minutes"}
    assert http.patch(path, json={**metadata, "punch_in": ms("2026-07-07", "06:00:00")}).status_code == 422
    # Correct to ABSENT, then restore a presence status with both punch instants.
    assert http.patch(path, json={**metadata, "status": "ABSENT"}).status_code == 200
    result = http.patch(path, json={
        **metadata, "status": "ON_DUTY", "punch_in": start,
        "punch_out": ms("2026-07-07", "02:29:42"),
    })
    assert result.status_code == 200, result.text
    assert result.json()["work_hours"] == 4.5 and result.json()["half_day"] is False
    assert len(result.json()["history"]) == 3


def test_calendar_leap_month_weekend_only_and_empty_metrics(api):
    http, database = api
    database.employees.insert_many([
        employee(joined="2024-02-15"), employee("EMP0002", "Empty", joined="2024-01-01"),
    ])
    result = http.get("/analytics/employees/EMP0001/monthly", params={"month": "2024-02"})
    assert result.status_code == 200 and result.json()["working_days"] == 11
    assert result.json()["attendance_pct"] == 0
    database.attendance_logs.insert_one(log("EMP0001", "2024-02-17", "WFH", 3.5, 35, 60, True))
    result = http.get("/analytics/employees/EMP0001/monthly", params={"month": "2024-02"}).json()
    assert result["present_days"] == 0 and result["total_late_minutes"] == 35 and result["total_overtime_minutes"] == 60
    summary = http.get("/analytics/departments/summary", params={"month": "2024-02"}).json()["items"]
    empty = next(row for row in summary if row["department"] == "Empty")
    assert empty["headcount"] == 1 and empty["avg_work_hours"] is None and empty["present_days"] == 0
    result = http.get("/analytics/departments/Engineering/trend", params={"from": "2024-02-17", "to": "2024-02-18"})
    assert len(result.json()["items"]) == 2
    assert all(row["attendance_rate"] is None and row["moving_avg_7d"] is None for row in result.json()["items"])
    result = http.get("/analytics/departments/Engineering/trend", params={"from": "2024-01-01", "to": "2024-04-01"})
    assert result.status_code == 200 and len(result.json()["items"]) == 92


def test_all_input_length_and_timestamp_bounds(api):
    http, database = api
    body = {key: value for key, value in employee().items() if key != "created_at"}
    invalid_fields = [
        ("name", ""), ("name", "x" * 101), ("department", ""), ("department", "x" * 51),
        ("email", "x" * 109 + "@example.com"), ("shift_start", "9:30"),
        ("joined_on", "2026-13-01"), ("emp_code", "EMP1234567"),
    ]
    for field, value in invalid_fields:
        response = http.post("/employees", json={**body, field: value})
        assert response.status_code == 422, (field, value, response.text)
    database.employees.insert_one(employee())
    for timestamp in [100000000000, 4102444800000]:
        result = http.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": timestamp})
        assert result.status_code == 201, result.text
        assert result.json()["punch_in"] == timestamp
    metadata = {"reason": "valid reason", "regularized_by": "hr"}
    record_date = main.attendance_date(main.instant(100000000000), employee())
    path = f"/attendance/EMP0001/{record_date}"
    for extra in [{"reason": "abcd"}, {"reason": "x" * 201}, {"regularized_by": ""}, {"regularized_by": "x" * 51}]:
        assert http.patch(path, json={**metadata, "status": "WFH", **extra}).status_code == 422
    for params in [{"limit": 0}, {"limit": 51}]:
        assert http.get("/analytics/leaderboard/late", params={"month": "2026-07", **params}).status_code == 422


def test_dotenv_cannot_override_real_environment(api, tmp_path):
    http, database = api
    root = Path(__file__).parents[1]
    copied_app = tmp_path / "app"
    shutil.copytree(root / "app", copied_app, ignore=shutil.ignore_patterns("__pycache__"))
    # These values are deliberately invalid, synthetic configuration, not secrets.
    (tmp_path / ".env").write_text("MONGO_URI=mongodb://127.0.0.1:1\nMONGO_DB=wrong_database\n")
    env = os.environ.copy()
    env["MONGO_URI"] = os.environ["TEST_MONGO_URI"]
    env["MONGO_DB"] = database.name
    # Preserve optional dependency paths when changing the subprocess's cwd.
    # A normal virtual environment needs no extra PYTHONPATH.
    env["PYTHONPATH"] = os.pathsep.join(
        str(Path(path).resolve()) for path in env.get("PYTHONPATH", "").split(os.pathsep) if path
    )
    command = (
        "from fastapi.testclient import TestClient; from app.main import app; "
        "http=TestClient(app); http.__enter__(); "
        "assert http.get('/health').status_code == 200; "
        "http.__exit__(None,None,None); print('environment priority passed')"
    )
    process = subprocess.run([sys.executable, "-c", command], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert "environment priority passed" in process.stdout
