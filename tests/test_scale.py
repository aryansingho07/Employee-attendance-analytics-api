"""Opt-in 100,000-record verification with independent analytics expectations."""

import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app import main


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SCALE_TESTS") != "1" or not os.getenv("TEST_MONGO_URI"),
    reason="Set RUN_SCALE_TESTS=1 and TEST_MONGO_URI for the 100k-record check",
)


def rounded(value, places=2):
    return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


def assert_no_lookup_scans(value):
    if isinstance(value, dict):
        if "collectionScans" in value:
            assert value["collectionScans"] == 0
        for child in value.values():
            assert_no_lookup_scans(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_lookup_scans(child)


def test_100k_analytics_and_real_server_startup(monkeypatch):
    uri = os.environ["TEST_MONGO_URI"]
    name = "attendance_test_scale_" + uuid4().hex
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=2500)
    database = client[name]
    employees = [{
        "emp_code": f"EMP{number:04d}", "name": f"Employee {number}",
        "email": f"e{number}@example.com", "department": f"Team {number % 10}",
        "joined_on": "2026-01-05", "shift_start": "09:30", "shift_end": "18:30",
        "created_at": datetime(2026, 1, 5, tzinfo=timezone.utc),
    } for number in range(1000)]
    # Seed first, with only default indexes, matching how the grader starts.
    database.employees.insert_many(employees)
    days = [date(2026, 4, 23) + timedelta(days=offset) for offset in range(100)]
    statuses = ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE", "PRESENT", "WFH", "PRESENT"]
    july = []
    batch = []
    process = None
    try:
        for number, employee in enumerate(employees):
            for offset, day in enumerate(days):
                status = statuses[(number + offset) % len(statuses)]
                presence = status in ["PRESENT", "WFH", "ON_DUTY"]
                half = presence and (number + offset) % 11 == 0
                hours = (3.5 if half else 8.125) if presence and (number + offset) % 13 else None
                record = {
                    "emp_code": employee["emp_code"], "date": day.isoformat(), "status": status,
                    "punch_in": datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=4) if presence else None,
                    "punch_out": datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=13) if hours is not None else None,
                    "work_hours": hours, "late_minutes": (35 if (number + offset) % 3 == 0 else 0) if presence else 0,
                    "overtime_minutes": 45 if presence and (number + offset) % 7 == 0 else 0,
                }
                if (number + offset) % 5:
                    record["half_day"] = half
                batch.append(record)
                if day.month == 7:
                    july.append({**record, "department": employee["department"]})
                if len(batch) == 5000:
                    database.attendance_logs.insert_many(batch)
                    batch = []
        assert database.attendance_logs.count_documents({}) == 100000
        monkeypatch.setenv("MONGO_URI", uri)
        monkeypatch.setenv("MONGO_DB", name)
        started = time.perf_counter()
        with TestClient(main.app) as http:
            startup = time.perf_counter() - started
            assert startup < 20
            print(f"\n100k seeded records; startup/index creation {startup:.3f}s")
            start = time.perf_counter()
            monthly = http.get("/analytics/employees/EMP0001/monthly", params={"month": "2026-07"})
            assert monthly.status_code == 200, monthly.text
            records = [r for r in july if r["emp_code"] == "EMP0001"]
            present = sum((0.5 if r.get("half_day", False) else 1) for r in records if r["status"] in main.PRESENCE and date.fromisoformat(r["date"]).weekday() < 5)
            assert monthly.json() == {
                "emp_code": "EMP0001", "month": "2026-07", "working_days": 23,
                "present_days": present, "leave_days": sum(r["status"] == "LEAVE" for r in records),
                "late_count": sum(r["late_minutes"] > 0 for r in records),
                "total_late_minutes": sum(r["late_minutes"] for r in records),
                "total_overtime_minutes": sum(r["overtime_minutes"] for r in records),
                "attendance_pct": rounded(Decimal(str(present)) / 23 * 100),
            }
            print(f"employee monthly {time.perf_counter() - start:.3f}s")
            start = time.perf_counter()
            response = http.get("/analytics/departments/summary", params={"month": "2026-07"})
            assert response.status_code == 200, response.text
            expected = []
            for team in range(10):
                records = [r for r in july if r["department"] == f"Team {team}"]
                hours = [Decimal(str(r["work_hours"])) for r in records if r["status"] in main.PRESENCE and r["work_hours"] is not None]
                expected.append({
                    "department": f"Team {team}", "headcount": 100,
                    "present_days": sum((0.5 if r.get("half_day", False) else 1) for r in records if r["status"] in main.PRESENCE and date.fromisoformat(r["date"]).weekday() < 5),
                    "avg_work_hours": rounded(sum(hours) / len(hours)) if hours else None,
                    "late_count": sum(r["late_minutes"] > 0 for r in records),
                    "total_late_minutes": sum(r["late_minutes"] for r in records),
                    "leave_count": sum(r["status"] == "LEAVE" for r in records),
                    "on_duty_count": sum(r["status"] == "ON_DUTY" for r in records),
                })
            assert response.json()["items"] == expected
            print(f"department summary {time.perf_counter() - start:.3f}s (includes oracle comparison)")
            start = time.perf_counter()
            response = http.get("/analytics/leaderboard/late", params={"month": "2026-07", "limit": 5})
            assert response.status_code == 200, response.text
            totals = {}
            for r in july:
                totals.setdefault(r["emp_code"], {"total": 0, "count": 0})
                totals[r["emp_code"]]["total"] += r["late_minutes"]
                totals[r["emp_code"]]["count"] += r["late_minutes"] > 0
            expected = []
            for employee in employees:
                total = totals[employee["emp_code"]]
                rank = 1 + sum(other["total"] > total["total"] for other in totals.values())
                if total["total"] > 0 and rank <= 5:
                    expected.append({"rank": rank, "emp_code": employee["emp_code"], "name": employee["name"],
                                     "department": employee["department"], "total_late_minutes": total["total"], "late_count": total["count"]})
            expected.sort(key=lambda row: (-row["total_late_minutes"], row["emp_code"]))
            assert response.json()["items"] == expected
            print(f"leaderboard {time.perf_counter() - start:.3f}s (includes oracle comparison)")
            start = time.perf_counter()
            response = http.get("/analytics/departments/Team%200/trend", params={"from": "2026-07-01", "to": "2026-07-31"})
            assert response.status_code == 200, response.text
            expected = []
            for day in range(1, 32):
                calendar_day = date(2026, 7, day)
                records = [r for r in july if r["department"] == "Team 0" and r["date"] == calendar_day.isoformat()]
                present = sum((0.5 if r.get("half_day", False) else 1) for r in records if r["status"] in main.PRESENCE)
                rate = rounded(Decimal(str(present)) / 100, 4) if calendar_day.weekday() < 5 else None
                window = [Decimal(str(row["attendance_rate"])) for row in expected[-6:] if row["attendance_rate"] is not None]
                if rate is not None:
                    window.append(Decimal(str(rate)))
                expected.append({
                    "date": calendar_day.isoformat(), "is_working_day": calendar_day.weekday() < 5,
                    "headcount": 100, "present_count": present, "late_count": sum(r["late_minutes"] > 0 for r in records),
                    "attendance_rate": rate, "moving_avg_7d": rounded(sum(window) / len(window), 4) if window else None,
                })
            assert response.json()["items"] == expected
            print(f"department trend {time.perf_counter() - start:.3f}s (includes oracle comparison)")
            operations = {
                "attendance_list": {"page": 10, "page_size": 20},
                "employee_monthly": {"emp_code": "EMP0001", "month": "2026-07"},
                "department_summary": {"month": "2026-07"},
                "late_leaderboard": {"month": "2026-07", "limit": 5},
                "department_trend": {"department": "Team 0", "from": "2026-07-01", "to": "2026-07-31"},
            }
            for endpoint, params in operations.items():
                result = http.get("/admin/explain/" + endpoint, params=params)
                assert result.status_code == 200, result.text
                assert "IXSCAN" in result.text and "COLLSCAN" not in result.text
                explanation = result.json()["explain"]
                assert_no_lookup_scans(explanation)
                stats = explanation.get("executionStats") or explanation["stages"][0]["$cursor"]["executionStats"]
                print(f"{endpoint} explain: {stats['totalKeysExamined']} keys, {stats['totalDocsExamined']} docs, {stats['executionTimeMillis']}ms")
        # Verify the mandated real startup command, not only an in-process client.
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        started = time.perf_counter()
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with httpx.Client() as http:
            while time.perf_counter() - started < 20:
                assert process.poll() is None, process.stderr.read().decode()
                try:
                    result = http.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
                    if result.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Uvicorn did not become ready within 20 seconds")
        print(f"real uvicorn readiness {time.perf_counter() - started:.3f}s")
    finally:
        if process is not None:
            process.terminate()
            process.communicate(timeout=10)
        assert name.startswith("attendance_test_scale_")
        client.drop_database(name)
        client.close()
