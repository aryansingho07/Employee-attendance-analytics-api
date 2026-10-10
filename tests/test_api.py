"""Rule tests and real-MongoDB integration tests; no mock aggregation engine."""

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from threading import Barrier
from uuid import uuid4

import pytest
import yaml
from jsonschema import Draft7Validator
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymongo import MongoClient

from app import main


def ist(day, clock):
    return datetime.fromisoformat(day + "T" + clock).replace(tzinfo=main.IST)


def ms(day, clock):
    return main.public(ist(day, clock))


def assert_no_lookup_scans(value):
    if isinstance(value, dict):
        if "collectionScans" in value:
            assert value["collectionScans"] == 0
        for child in value.values():
            assert_no_lookup_scans(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_lookup_scans(child)


def employee(code="EMP0001", department="Engineering", joined="2026-07-01", **changes):
    return {
        "emp_code": code, "name": code, "email": code + "@example.com",
        "department": department, "joined_on": joined, "shift_start": "09:30",
        "shift_end": "18:30", "created_at": main.instant(), **changes,
    }


def log(code, day, status="PRESENT", hours=8, late=0, overtime=0, half=False):
    return {
        "emp_code": code, "date": day, "status": status,
        "punch_in": ist(day, "09:30:00") if status in main.PRESENCE else None,
        "punch_out": ist(day, "18:30:00") if hours is not None and status in main.PRESENCE else None,
        "work_hours": hours if status in main.PRESENCE else None,
        "late_minutes": late, "overtime_minutes": overtime, "half_day": half,
    }


@pytest.fixture
def api(monkeypatch):
    uri = os.getenv("TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set TEST_MONGO_URI to run integration tests against MongoDB 6+")
    name = "attendance_test_" + uuid4().hex
    monkeypatch.setenv("MONGO_URI", uri)
    monkeypatch.setenv("MONGO_DB", name)
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=2500)
    try:
        with TestClient(main.app) as http:
            yield http, client[name]
    finally:
        # Only the uniquely named test database created by this fixture is dropped.
        assert name.startswith("attendance_test_")
        client.drop_database(name)
        client.close()


@pytest.mark.parametrize("clock, expected", [
    ("09:39:59", 0), ("09:40:00", 0), ("09:40:01", 10), ("10:15:59", 45),
])
def test_late_boundaries(clock, expected):
    start, _ = main.shift_bounds(employee(), "2026-07-06")
    assert main.compute_late_minutes(ist("2026-07-06", clock), start) == expected


@pytest.mark.parametrize("seconds, hours, half", [(16182, 4.50, False), (16218, 4.51, False), (16181, 4.49, True)])
def test_half_up_and_half_day(seconds, hours, half):
    start = ist("2026-07-06", "09:30:00")
    result = main.derived(employee(), "2026-07-06", start, start + timedelta(seconds=seconds))
    assert result["work_hours"] == hours
    assert result["half_day"] is half


@pytest.mark.parametrize("value", [1783312500, 1783312500000.0, "1783312500000", True, None, 4102444800001])
def test_timestamp_is_strict(value):
    with pytest.raises(ValidationError):
        main.PunchInIn(emp_code="EMP0001", punched_at=value)


def test_calendar_validation():
    for changes in [
        {"joined_on": "2026-02-30"}, {"joined_on": "20260706"}, {"shift_start": "24:00"},
        {"email": "broken"}, {"emp_code": "EMP1"}, {"shift_end": "09:30"},
    ]:
        with pytest.raises(ValidationError):
            main.EmployeeIn(**{**employee(), **changes})


def test_overnight_attribution_and_overtime():
    night = employee(shift_start="22:00", shift_end="06:00")
    assert main.attendance_date(ist("2026-07-07", "05:59:59"), night) == "2026-07-06"
    assert main.attendance_date(ist("2026-07-07", "06:00:00"), night) == "2026-07-07"
    _, end = main.shift_bounds(night, "2026-07-06")
    assert main.compute_overtime(ist("2026-07-07", "06:29:59"), end) == 0
    assert main.compute_overtime(ist("2026-07-07", "06:30:00"), end) == 30
    assert main.compute_overtime(ist("2026-07-07", "06:40:59"), end) == 40


def test_employees_validation_and_pagination(api):
    http, database = api
    assert http.get("/health").json() == {"status": "ok"}
    for code, dept in [("EMP0003", "Sales"), ("EMP0002", "Engineering"), ("EMP0001", "Engineering")]:
        result = http.post("/employees", json={key: value for key, value in employee(code, dept).items() if key != "created_at"})
        assert result.status_code == 201, result.text
        assert isinstance(result.json()["created_at"], int)
        assert "_id" not in result.json()
    result = http.get("/employees", params={"department": "Engineering", "page_size": 1}).json()
    assert result["total"] == 2
    assert result["items"][0]["emp_code"] == "EMP0001"
    assert http.get("/employees", params={"page_size": 1, "page": 2}).json()["items"][0]["emp_code"] == "EMP0002"
    for params in [{"page": 0}, {"page_size": 101}, {"page_size": 0}]:
        assert http.get("/employees", params=params).status_code == 422
    assert http.post("/employees", json={"emp_code": "EMP1"}).status_code == 422
    main.create_indexes(database)  # Startup indexes are idempotent.


def test_duplicate_creates_and_punches_are_atomic(api):
    http, database = api
    body = {key: value for key, value in employee().items() if key != "created_at"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: http.post("/employees", json=body).status_code, range(8)))
    assert codes.count(201) == 1 and codes.count(409) == 7
    punch = {"emp_code": "EMP0001", "punched_at": ms("2026-07-06", "09:40:01") + 900}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: http.post("/attendance/punch-in", json=punch), range(8)))
    assert sum(result.status_code == 201 for result in results) == 1
    assert sum(result.status_code == 409 for result in results) == 7
    record = next(result.json() for result in results if result.status_code == 201)
    assert record["late_minutes"] == 10
    assert record["punch_in"] == punch["punched_at"] // 1000 * 1000
    assert record["history"] == [] and "id" not in record and "_id" not in record
    out = {"emp_code": "EMP0001", "punched_at": ms("2026-07-06", "19:00:00")}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: http.post("/attendance/punch-out", json=out), range(8)))
    assert sum(result.status_code == 200 for result in results) == 1
    assert sum(result.status_code == 409 for result in results) == 7
    record = next(result.json() for result in results if result.status_code == 200)
    assert record["overtime_minutes"] == 30 and record["history"] == []
    assert database.attendance_logs.count_documents({}) == 1


def test_punch_errors_overnight_and_duration(api):
    http, database = api
    database.employees.insert_one(employee(shift_start="22:00", shift_end="06:00"))
    assert http.post("/attendance/punch-in", json={"emp_code": "EMP9999"}).status_code == 404
    assert http.post("/attendance/punch-out", json={"emp_code": "EMP0001"}).status_code == 404
    for timestamp in [1783312500, "1783312500000", 1783312500000.0, None]:
        assert http.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": timestamp}).status_code == 422
    for status in ["ABSENT", "LEAVE", "INVALID"]:
        assert http.post("/attendance/punch-in", json={"emp_code": "EMP0001", "status": status}).status_code == 422
    start = ms("2026-07-06", "22:00:00")
    assert http.post("/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": start}).status_code == 201
    assert http.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": start}).status_code == 422
    assert http.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": start + 86401000}).status_code == 422
    result = http.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": ms("2026-07-07", "06:40:00")})
    assert result.status_code == 200, result.text
    assert result.json()["date"] == "2026-07-06"
    assert result.json()["overtime_minutes"] == 40
    assert result.json()["work_hours"] == 8.67


def test_correction_history_validation_and_clear(api):
    http, database = api
    database.employees.insert_one(employee())
    database.attendance_logs.insert_one(log("EMP0001", "2026-07-06", hours=None, late=0))
    path = "/attendance/EMP0001/2026-07-06"
    metadata = {"reason": "device correction", "regularized_by": "hr.admin"}
    assert http.patch(path, json=metadata).status_code == 422
    assert http.patch(path, json={**metadata, "punch_in": ms("2026-07-07", "09:30:00")}).status_code == 422
    assert http.patch(path, json={**metadata, "punch_out": ms("2026-07-06", "09:30:00")}).status_code == 422
    assert http.patch(path, json={**metadata, "status": "LEAVE", "punch_in": ms("2026-07-06", "09:30:00")}).status_code == 422
    assert http.patch(path, json={**metadata, "punch_in": None}).status_code == 422
    result = http.patch(path, json={**metadata, "punch_in": ms("2026-07-06", "10:15:59"), "late_minutes": 999})
    assert result.status_code == 200, result.text
    assert result.json()["late_minutes"] == 45
    history = result.json()["history"]
    assert len(history) == 1 and set(history[0]["changes"]) == {"punch_in", "late_minutes"}
    assert history[0]["changes"]["punch_in"]["from"] == ms("2026-07-06", "09:30:00")
    assert isinstance(history[0]["at"], int)
    stored = database.attendance_logs.find_one({})
    assert isinstance(stored["history"][0]["changes"]["punch_in"]["to"], datetime)
    result = http.patch(path, json={**metadata, "status": "LEAVE"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["punch_in"] is None and body["punch_out"] is None
    assert body["work_hours"] is None and body["half_day"] is False and body["late_minutes"] == 0
    assert len(body["history"]) == 2 and body["history"][0] == history[0]
    assert http.patch(path, json={**metadata, "status": "WFH"}).status_code == 422
    assert http.patch("/attendance/EMP9999/2026-07-06", json=metadata).status_code == 404
    assert http.patch("/attendance/EMP0001/2026-07-08", json=metadata).status_code == 404


def test_simultaneous_corrections_keep_history(api, monkeypatch):
    http, database = api
    database.employees.insert_one(employee())
    database.attendance_logs.insert_one(log("EMP0001", "2026-07-06", hours=None))
    barrier = Barrier(2)
    original = main.snapshot_filter

    def synchronize(record):
        condition = original(record)
        barrier.wait(timeout=10)
        return condition

    monkeypatch.setattr(main, "snapshot_filter", synchronize)
    def correct(clock):
        return http.patch("/attendance/EMP0001/2026-07-06", json={
            "reason": "device correction", "regularized_by": clock,
            "punch_in": ms("2026-07-06", clock),
        })
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, ["10:00:00", "10:30:00"]))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert len(database.attendance_logs.find_one({})["history"]) == 1


def seed_analytics(database):
    database.employees.insert_many([
        employee(), employee("EMP0002", joined="2026-07-13"),
        employee("EMP0003", joined="2026-08-01"), employee("EMP0004"),
        employee("EMP0005", "Sales"), employee("EMP0006", "Future", "2026-08-01"),
    ])
    database.attendance_logs.insert_many([
        log("EMP0001", "2026-07-06", hours=8.125),
        log("EMP0001", "2026-07-07", "WFH", 3.5, 35, half=True),
        log("EMP0001", "2026-07-08", "LEAVE"),
        log("EMP0001", "2026-07-11", "ON_DUTY", 5, 20, 60),
        log("EMP0002", "2026-07-14", hours=9, late=55),
        log("EMP0002", "2026-07-15", hours=7),
        log("EMP0005", "2026-07-06", late=35), log("EMP0005", "2026-07-07", late=20),
        log("EMP9999", "2026-07-06", late=999),
    ])


def test_analytics_joiners_weekends_record_average_and_ties(api):
    http, database = api
    seed_analytics(database)
    response = http.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "emp_code": "EMP0001", "month": "2026-07", "working_days": 23,
        "present_days": 1.5, "leave_days": 1, "late_count": 2,
        "total_late_minutes": 55, "total_overtime_minutes": 60, "attendance_pct": 6.52,
    }
    joiner = http.get("/analytics/employees/EMP0002/monthly", params={"month": "2026-07"}).json()
    assert joiner["working_days"] == 15 and joiner["present_days"] == 2
    future = http.get("/analytics/employees/EMP0003/monthly", params={"month": "2026-07"}).json()
    assert future["working_days"] == 0 and future["attendance_pct"] is None
    zero = http.get("/analytics/employees/EMP0004/monthly", params={"month": "2026-07"}).json()
    assert zero["present_days"] == 0 and zero["attendance_pct"] == 0
    result = http.get("/analytics/departments/summary", params={"month": "2026-07"})
    assert result.status_code == 200, result.text
    assert [row["department"] for row in result.json()["items"]] == ["Engineering", "Sales"]
    assert result.json()["items"][0] == {
        "department": "Engineering", "headcount": 3, "present_days": 3.5,
        "avg_work_hours": 6.53, "late_count": 3, "total_late_minutes": 110,
        "leave_count": 1, "on_duty_count": 1,
    }
    assert http.get("/analytics/departments/summary", params={"month": "2026-07", "department": "Future"}).json()["items"] == []
    result = http.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 1})
    assert result.status_code == 200, result.text
    rows = result.json()["items"]
    assert [row["emp_code"] for row in rows] == ["EMP0001", "EMP0002", "EMP0005"]
    assert [row["rank"] for row in rows] == [1, 1, 1]
    database.attendance_logs.insert_one(log("EMP0004", "2026-07-20", late=10))
    rows = http.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 4}).json()["items"]
    assert [row["rank"] for row in rows] == [1, 1, 1, 4]
    rows = http.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 1, "department": "Engineering"}).json()["items"]
    assert [row["emp_code"] for row in rows] == ["EMP0001", "EMP0002"]


def test_trend_gaps_headcount_weekends_moving_average(api):
    http, database = api
    seed_analytics(database)
    result = http.get("/analytics/departments/Engineering/trend", params={"from": "2026-07-06", "to": "2026-07-15"})
    assert result.status_code == 200, result.text
    rows = result.json()["items"]
    assert len(rows) == 10
    assert [row["date"] for row in rows] == [f"2026-07-{day:02d}" for day in range(6, 16)]
    assert rows[0]["headcount"] == 2 and rows[7]["headcount"] == 3
    assert rows[0]["attendance_rate"] == 0.5 and rows[1]["attendance_rate"] == 0.25
    assert rows[1]["moving_avg_7d"] == 0.375
    assert rows[2]["attendance_rate"] == 0.0
    assert rows[5]["present_count"] == 1 and rows[5]["late_count"] == 1
    assert rows[5]["attendance_rate"] is None and rows[5]["is_working_day"] is False
    for i, row in enumerate(rows):
        non_null = [Decimal(str(r["attendance_rate"])) for r in rows[max(0, i - 6):i + 1] if r["attendance_rate"] is not None]
        expected = float((sum(non_null) / len(non_null)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if non_null else None
        assert row["moving_avg_7d"] == expected
    result = http.get("/analytics/departments/Future/trend", params={"from": "2026-07-06", "to": "2026-07-12"})
    assert len(result.json()["items"]) == 7
    assert all(row["headcount"] == 0 and row["attendance_rate"] is None and row["moving_avg_7d"] is None for row in result.json()["items"])
    result = http.get("/analytics/departments/Engineering/trend", params={"from": "2026-07-20", "to": "2026-07-21"})
    assert all(row["present_count"] == 0 and row["attendance_rate"] == 0 for row in result.json()["items"])


def test_attendance_filters_order_missing_defaults_and_errors(api):
    http, database = api
    seed_analytics(database)
    result = http.get("/attendance", params={"date_from": "2026-07-06", "date_to": "2026-07-07", "page_size": 2}).json()
    assert result["total"] == 5
    assert [(row["date"], row["emp_code"]) for row in result["items"]] == [("2026-07-07", "EMP0001"), ("2026-07-07", "EMP0005")]
    assert result["items"][0]["history"] == []
    database.attendance_logs.update_one({"emp_code": "EMP0001", "date": "2026-07-06"}, {"$unset": {"half_day": 1, "late_minutes": 1}})
    record = http.get("/attendance", params={"emp_code": "EMP0001", "date_from": "2026-07-06", "date_to": "2026-07-06"}).json()["items"][0]
    assert record["half_day"] is False and record["late_minutes"] == 0
    for params in [{"date_from": "2026-07-08", "date_to": "2026-07-07"}, {"date_from": "2026-02-30"}, {"status": "UNKNOWN"}, {"page": 0}]:
        assert http.get("/attendance", params=params).status_code == 422
    assert http.get("/analytics/employees/EMP9999/monthly", params={"month": "2026-07"}).status_code == 404
    for month in ["2026-13", "0000-01", "2026-7"]:
        assert http.get("/analytics/departments/summary", params={"month": month}).status_code == 422
    for params in [{"from": "2026-07-08", "to": "2026-07-07"}, {"from": "2026-01-01", "to": "2026-04-03"}]:
        assert http.get("/analytics/departments/Engineering/trend", params=params).status_code == 422
    assert http.get("/analytics/departments/Unknown/trend", params={"from": "2026-07-06", "to": "2026-07-06"}).status_code == 404


def test_explain_reuses_filters_and_indexes(api):
    http, database = api
    seed_analytics(database)
    operations = {
        "attendance_list": {"emp_code": "EMP0001", "page": 2, "page_size": 1, "status": "PRESENT", "date_from": "2026-07-01"},
        "employee_monthly": {"emp_code": "EMP0001", "month": "2026-07"},
        "department_summary": {"month": "2026-07"},
        "late_leaderboard": {"month": "2026-07", "limit": 1, "department": "Engineering"},
        "department_trend": {"department": "Engineering", "from": "2026-07-06", "to": "2026-07-15"},
    }
    for endpoint, params in operations.items():
        result = http.get("/admin/explain/" + endpoint, params=params)
        assert result.status_code == 200, result.text
        assert "IXSCAN" in result.text and "COLLSCAN" not in result.text
        assert_no_lookup_scans(result.json()["explain"])
        if endpoint == "attendance_list":
            command = result.json()["explain"]["command"]
            assert command["skip"] == 1 and command["limit"] == 1
            assert command["filter"]["status"] == "PRESENT"
    assert http.get("/admin/explain/employee_monthly", params={"month": "2026-07"}).status_code == 422
    assert http.get("/admin/explain/department_trend", params={"department": "Engineering"}).status_code == 422
    assert http.get("/admin/explain/invalid").status_code == 422


def test_health_failure_is_503(monkeypatch):
    from pymongo.errors import ServerSelectionTimeoutError
    class Offline:
        def command(self, *args):
            raise ServerSelectionTimeoutError("offline")
    monkeypatch.setattr(main, "db", Offline())
    http = TestClient(main.app)  # No lifespan: deliberately replace only the database.
    assert http.get("/health").status_code == 503


def test_explain_serializes_replica_set_metadata(monkeypatch):
    from bson import Binary, Timestamp
    class ReplicaMetadata:
        def command(self, operation):
            return {"$clusterTime": {
                "clusterTime": Timestamp(1783312500, 1),
                "signature": {"hash": Binary(b"\xff\x00", 0), "keyId": 1},
            }}
    monkeypatch.setattr(main, "db", ReplicaMetadata())
    result = TestClient(main.app).get("/admin/explain/attendance_list")
    assert result.status_code == 200, result.text
    metadata = result.json()["explain"]["$clusterTime"]
    assert metadata["clusterTime"] == {"$timestamp": {"t": 1783312500, "i": 1}}
    assert metadata["signature"]["hash"] == {"$binary": {"base64": "/wA=", "subType": "00"}}


def test_response_shapes_against_supplied_openapi(api):
    http, database = api
    seed_analytics(database)
    contract = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text())

    def json_schema(value):
        if isinstance(value, list):
            return [json_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: json_schema(item) for key, item in value.items() if key != "nullable"}
        if value.get("nullable") and "type" in result:
            result["type"] = [result["type"], "null"]
        return result

    root = json_schema(contract)
    payload = {key: value for key, value in employee("EMP0007").items() if key != "created_at"}
    checks = [
        ("/health", "get", http.get("/health")),
        ("/employees", "post", http.post("/employees", json=payload)),
        ("/employees", "get", http.get("/employees")),
        ("/attendance/punch-in", "post", http.post("/attendance/punch-in", json={"emp_code": "EMP0007", "punched_at": ms("2026-07-06", "09:30:00")})),
        ("/attendance/punch-out", "post", http.post("/attendance/punch-out", json={"emp_code": "EMP0007", "punched_at": ms("2026-07-06", "18:30:00")})),
        ("/attendance/{emp_code}/{date}", "patch", http.patch("/attendance/EMP0007/2026-07-06", json={"status": "WFH", "reason": "device correction", "regularized_by": "hr"})),
        ("/attendance", "get", http.get("/attendance")),
        ("/analytics/employees/{emp_code}/monthly", "get", http.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})),
        ("/analytics/departments/summary", "get", http.get("/analytics/departments/summary", params={"month": "2026-07"})),
        ("/analytics/leaderboard/late", "get", http.get("/analytics/leaderboard/late", params={"month": "2026-07"})),
        ("/analytics/departments/{department}/trend", "get", http.get("/analytics/departments/Engineering/trend", params={"from": "2026-07-06", "to": "2026-07-12"})),
        ("/admin/explain/{endpoint}", "get", http.get("/admin/explain/attendance_list")),
    ]
    for path, method, response in checks:
        assert response.status_code in (200, 201), response.text
        schema = root["paths"][path][method]["responses"][str(response.status_code)]["content"]["application/json"]["schema"]
        validator = Draft7Validator({"components": root["components"], **schema})
        validator.validate(response.json())
        if path != "/admin/explain/{endpoint}":
            assert '"_id"' not in response.text and '"id"' not in response.text
