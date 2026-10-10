"""Employee Attendance & Analytics API; application and query builders in one file."""

import os
from calendar import monthrange
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from bson import Decimal128, ObjectId, json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(override=False)
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
ExplainEndpoint = Literal[
    "attendance_list", "employee_monthly", "department_summary",
    "late_leaderboard", "department_trend",
]
Millis = Annotated[int, Field(strict=True, ge=100000000000, le=4102444800000)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Limit = Annotated[int, Query(ge=1, le=50)]


def valid_date(value: str) -> str:
    Date.fromisoformat(value)  # Reject impossible dates as well as bad syntax.
    return value


def valid_month(value: str) -> str:
    Date.fromisoformat(value + "-01")
    return value


CalendarDate = Annotated[
    str, Field(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    AfterValidator(valid_date),
]
Month = Annotated[
    str, Field(strict=True, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$"),
    AfterValidator(valid_month),
]
ShiftTime = Annotated[
    str, Field(strict=True, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"),
]


class InputModel(BaseModel):
    # The contract explicitly says to ignore client-supplied derived fields.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)


class EmployeeIn(InputModel):
    emp_code: Annotated[str, Field(strict=True, pattern=r"^EMP[0-9]{4,6}$")]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=100)]
    email: Annotated[str, Field(
        strict=True, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )]
    department: Annotated[str, Field(strict=True, min_length=1, max_length=50)]
    shift_start: ShiftTime = "09:30"
    shift_end: ShiftTime = "18:30"
    joined_on: CalendarDate

    @model_validator(mode="after")
    def different_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(InputModel):
    emp_code: str
    # None is the omitted-field default; explicit JSON null is not a Millis.
    punched_at: Millis = None
    status: PresenceStatus = "PRESENT"


class PunchOutIn(InputModel):
    emp_code: str
    punched_at: Millis = None


class RegularizeIn(InputModel):
    status: Status = None
    punch_in: Millis = None
    punch_out: Millis = None
    reason: Annotated[str, Field(strict=True, min_length=5, max_length=200)]
    regularized_by: Annotated[str, Field(strict=True, min_length=1, max_length=50)]


db = None


def create_indexes(database):
    employees = database.employees
    employees.create_index([("emp_code", ASCENDING)], unique=True, name="employee_code")
    employees.create_index([("department", 1), ("emp_code", 1)], name="department_code")
    employees.create_index([("department", 1), ("joined_on", 1)], name="department_joined")
    employees.create_index([("joined_on", 1), ("department", 1)], name="joined_department")
    logs = database.attendance_logs
    logs.create_index([("emp_code", 1), ("date", -1)], unique=True, name="employee_date")
    logs.create_index([("date", -1), ("emp_code", 1)], name="date_employee")
    logs.create_index([("status", 1), ("date", -1), ("emp_code", 1)], name="status_date_employee")
    logs.create_index([("emp_code", 1), ("status", 1), ("date", -1)], name="employee_status_date")
    logs.create_index([("emp_code", 1), ("punch_in", -1)], name="employee_punch")


@asynccontextmanager
async def lifespan(application):
    global db
    uri, name = os.getenv("MONGO_URI"), os.getenv("MONGO_DB")
    if not uri or not name:
        raise RuntimeError("Set MONGO_URI and MONGO_DB before starting the API")
    client = MongoClient(
        uri, tz_aware=True, tzinfo=UTC, serverSelectionTimeoutMS=2500,
        connectTimeoutMS=2500, socketTimeoutMS=10000,
    )
    db = client[name]
    try:
        create_indexes(db)
        yield
    finally:
        client.close()


app = FastAPI(
    title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan,
)


@app.exception_handler(PyMongoError)
async def database_error(request: Request, error: PyMongoError):
    return JSONResponse(status_code=503, content={"detail": "MongoDB unavailable"})


def invalid(message: str, field: str, source: str = "body"):
    raise HTTPException(422, detail=[{
        "type": "value_error", "loc": [source, field], "msg": message,
    }])


def instant(value: int | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    return EPOCH + timedelta(seconds=value // 1000)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def public(value):
    """Serialize BSON recursively, including datetime values inside audit changes."""
    if isinstance(value, datetime):
        return (aware(value) - EPOCH) // timedelta(milliseconds=1)
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, ObjectId):
        return str(value)  # Raw explain may contain BSON; resource projections omit _id.
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # Atlas/replica-set explain responses may contain cluster Timestamp and
    # Binary signature values. Preserve those in MongoDB Extended JSON form.
    return public(json_util.default(value))


def employee_response(document):
    return public({key: document[key] for key in (
        "emp_code", "name", "email", "department", "shift_start", "shift_end",
        "joined_on", "created_at",
    )})


def attendance_response(document):
    defaults = {
        "punch_in": None, "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": [],
    }
    return public({
        "emp_code": document["emp_code"], "date": document["date"],
        "status": document["status"],
        **{key: document.get(key, value) for key, value in defaults.items()},
    })


def get_employee(emp_code: str):
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Unknown employee")
    return employee


def attendance_date(punch: datetime, employee) -> str:
    local = aware(punch).astimezone(IST)
    day = local.date()
    if employee["shift_end"] <= employee["shift_start"]:
        if local.time() < time.fromisoformat(employee["shift_end"]):
            day -= timedelta(days=1)
    return day.isoformat()


def shift_bounds(employee, day: str):
    start = datetime.combine(Date.fromisoformat(day), time.fromisoformat(employee["shift_start"]), IST)
    end = datetime.combine(Date.fromisoformat(day), time.fromisoformat(employee["shift_end"]), IST)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def compute_late_minutes(punch_in: datetime, shift_start: datetime) -> int:
    seconds = int((aware(punch_in) - shift_start).total_seconds())
    return seconds // 60 if seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = (aware(punch_out) - aware(punch_in)) // timedelta(seconds=1)
    return float((Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: datetime) -> int:
    minutes = (aware(punch_out) - shift_end) // timedelta(minutes=1)
    return minutes if minutes >= 30 else 0


def check_duration(punch_in: datetime, punch_out: datetime):
    duration = aware(punch_out) - aware(punch_in)
    if duration <= timedelta(0) or duration > timedelta(hours=24):
        invalid("punch_out must be after punch_in and within 24 hours", "punch_out")


def derived(employee, day: str, punch_in, punch_out):
    if punch_in is None:
        return {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    start, end = shift_bounds(employee, day)
    hours = compute_work_hours(punch_in, punch_out) if punch_out is not None else None
    return {
        "late_minutes": compute_late_minutes(punch_in, start), "work_hours": hours,
        "overtime_minutes": compute_overtime(punch_out, end) if punch_out is not None else 0,
        "half_day": hours is not None and hours < 4.50,
    }


MUTABLE = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")


def snapshot_filter(document):
    # Compare history too: this detects an edit even if values were changed back.
    # $exists distinguishes a missing seeded field from an explicitly stored null.
    return {"_id": document["_id"], **{
        key: document[key] if key in document else {"$exists": False}
        for key in (*MUTABLE, "history")
    }}


def attendance_operation(emp_code=None, date_from=None, date_to=None, status=None, page=1, page_size=20):
    if date_from and date_to and date_from > date_to:
        invalid("date_from must be on or before date_to", "date_to", "query")
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        query["date"] = {}
        if date_from is not None:
            query["date"]["$gte"] = date_from
        if date_to is not None:
            query["date"]["$lte"] = date_to
    if status is not None:
        query["status"] = status
    hint = ("employee_status_date" if status is not None else "employee_date") if emp_code is not None else (
        "status_date_employee" if status is not None else "date_employee"
    )
    return {
        "find": "attendance_logs", "filter": query, "projection": {"_id": 0},
        "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size,
        "limit": page_size, "hint": hint,
    }


@app.get("/health")
def health():
    db.command("ping")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    document = body.model_dump()
    document["created_at"] = instant()
    try:
        db.employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists") from None
    return employee_response(document)


@app.get("/employees")
def list_employees(department: str | None = None, page: Page = 1, page_size: PageSize = 20):
    query = {} if department is None else {"department": department}
    hint = "employee_code" if department is None else "department_code"
    cursor = db.employees.find(query, {"_id": 0}).hint(hint).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    return {
        "items": [employee_response(document) for document in cursor],
        "total": db.employees.count_documents(query, hint=hint), "page": page, "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = get_employee(body.emp_code)
    timestamp = instant(body.punched_at)
    day = attendance_date(timestamp, employee)
    document = {
        "emp_code": body.emp_code, "date": day, "status": body.status,
        "punch_in": timestamp, "punch_out": None, "history": [],
        **derived(employee, day, timestamp, None),
    }
    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this date") from None
    return attendance_response(document)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = get_employee(body.emp_code)
    timestamp = instant(body.punched_at)
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$type": "date", "$lte": timestamp}},
        sort=[("punch_in", DESCENDING)], hint="employee_punch",
    )
    if record is None:
        raise HTTPException(404, "No punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    check_duration(record["punch_in"], timestamp)
    updated = db.attendance_logs.find_one_and_update(
        snapshot_filter(record),
        {"$set": {"punch_out": timestamp, **derived(employee, record["date"], record["punch_in"], timestamp)}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "Record changed concurrently; retry")
    return attendance_response(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None, date_from: CalendarDate | None = None,
    date_to: CalendarDate | None = None, status: Status | None = None,
    page: Page = 1, page_size: PageSize = 20,
):
    operation = attendance_operation(emp_code, date_from, date_to, status, page, page_size)
    cursor = db.attendance_logs.find(operation["filter"], operation["projection"]).hint(operation["hint"]).sort(
        list(operation["sort"].items())
    ).skip(operation["skip"]).limit(operation["limit"])
    return {
        "items": [attendance_response(document) for document in cursor],
        "total": db.attendance_logs.count_documents(operation["filter"], hint=operation["hint"]),
        "page": page, "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: CalendarDate, body: RegularizeIn):
    employee = get_employee(emp_code)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "No attendance record for this date")
    final = {key: record.get(key, default) for key, default in {
        "status": None, "punch_in": None, "punch_out": None, "work_hours": None,
        "late_minutes": 0, "overtime_minutes": 0, "half_day": False,
    }.items()}
    fields = body.model_fields_set
    if "status" in fields:
        final["status"] = body.status
    if final["status"] not in PRESENCE:
        if fields & {"punch_in", "punch_out"}:
            invalid("ABSENT and LEAVE cannot have punch times", "status")
        final["punch_in"] = final["punch_out"] = None
    else:
        for key in ("punch_in", "punch_out"):
            if key in fields:
                final[key] = instant(getattr(body, key))
            elif final[key] is not None:
                final[key] = aware(final[key]).replace(microsecond=0)
        if final["punch_in"] is None:
            invalid("Presence status requires punch_in", "punch_in")
        if attendance_date(final["punch_in"], employee) != date:
            invalid("punch_in must stay on the attendance date", "punch_in")
        if final["punch_out"] is not None:
            check_duration(final["punch_in"], final["punch_out"])
    final.update(derived(employee, date, final["punch_in"], final["punch_out"]))
    before = attendance_response(record)
    changes = {
        key: {"from": record.get(key, before[key]), "to": final[key]}
        for key in MUTABLE if public(final[key]) != before[key]
    }
    if not changes:
        invalid("Correction changes nothing", "status")
    entry = {"at": instant(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    updated = db.attendance_logs.find_one_and_update(
        snapshot_filter(record), {"$set": final, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "Record changed concurrently; retry")
    return attendance_response(updated)


# Aggregation expressions. Decimal arithmetic avoids binary-float half ties;
# MongoDB $round uses ties-to-even, so apply positive half-up explicitly.
def half_up(expression, places=2, as_double=True):
    scale = 10 ** places
    rounded = {"$divide": [
        {"$floor": {"$add": [
            {"$multiply": [{"$toDecimal": expression}, scale]}, Decimal128("0.5"),
        ]}}, scale,
    ]}
    return {"$toDouble": rounded} if as_double else rounded


def month_bounds(month: str):
    first = Date.fromisoformat(month + "-01")
    last = first.replace(day=monthrange(first.year, first.month)[1])
    return first, last


def mongo_date(expression):
    return {"$dateFromString": {"dateString": expression, "format": "%Y-%m-%d", "timezone": "UTC"}}


def weekday(expression):
    return {"$in": [{"$dayOfWeek": mongo_date(expression)}, [2, 3, 4, 5, 6]]}


def presence_weight(weekdays_only=False):
    condition = {"$in": ["$status", PRESENCE]}
    if weekdays_only:
        condition = {"$and": [condition, weekday("$date")]}
    return {"$cond": [condition, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}


def metrics_group():
    has_hours = {"$and": [
        {"$in": ["$status", PRESENCE]}, {"$ne": [{"$ifNull": ["$work_hours", None]}, None]},
    ]}
    return {"$group": {
        "_id": None,
        "present_days": {"$sum": presence_weight(True)},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        "hours_sum": {"$sum": {"$cond": [has_hours, {"$toDecimal": "$work_hours"}, 0]}},
        "hours_count": {"$sum": {"$cond": [has_hours, 1, 0]}},
    }}


def logs_lookup(first: str, last: str, stages, output="metrics"):
    return {"$lookup": {
        "from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code",
        "pipeline": [{"$match": {"date": {"$gte": first, "$lte": last}}}, *stages], "as": output,
    }}


def aggregate_operation(collection, pipeline, hint):
    return {"aggregate": collection, "pipeline": pipeline, "cursor": {}, "hint": hint, "allowDiskUse": True}


def monthly_operation(emp_code: str, month: str):
    first, last = month_bounds(month)
    calendar = {"$map": {
        "input": {"$range": [0, last.day]}, "as": "offset",
        "in": {"$dateAdd": {"startDate": mongo_date(first.isoformat()), "unit": "day", "amount": "$$offset"}},
    }}
    working_days = {"$size": {"$filter": {
        "input": calendar, "as": "day", "cond": {"$and": [
            {"$gte": ["$$day", mongo_date("$joined_on")]},
            {"$in": [{"$dayOfWeek": "$$day"}, [2, 3, 4, 5, 6]]},
        ]},
    }}}
    pipeline = [
        {"$match": {"emp_code": emp_code}},
        logs_lookup(first.isoformat(), last.isoformat(), [metrics_group()]),
        {"$set": {"metrics": {"$ifNull": [{"$arrayElemAt": ["$metrics", 0]}, {}]}, "working_days": working_days}},
        {"$set": {"present_days": {"$ifNull": ["$metrics.present_days", 0]}}},
        {"$project": {
            "_id": 0, "emp_code": 1, "month": {"$literal": month}, "working_days": 1,
            "present_days": half_up("$present_days"),
            **{key: {"$ifNull": ["$metrics." + key, 0]} for key in (
                "leave_days", "late_count", "total_late_minutes", "total_overtime_minutes",
            )},
            "attendance_pct": {"$cond": [
                {"$gt": ["$working_days", 0]},
                half_up({"$multiply": [{"$divide": [{"$toDecimal": "$present_days"}, "$working_days"]}, 100]}), None,
            ]},
        }},
    ]
    return aggregate_operation("employees", pipeline, "employee_code")


def summary_operation(month: str, department=None):
    first, last = month_bounds(month)
    query = {"joined_on": {"$lte": last.isoformat()}}
    if department is not None:
        query["department"] = department
    sums = {
        key: {"$sum": {"$ifNull": ["$metrics." + key, 0]}}
        for key in ("present_days", "late_count", "total_late_minutes", "on_duty_count", "hours_sum", "hours_count")
    }
    pipeline = [
        {"$match": query}, logs_lookup(first.isoformat(), last.isoformat(), [metrics_group()]),
        {"$set": {"metrics": {"$ifNull": [{"$arrayElemAt": ["$metrics", 0]}, {}]}}},
        {"$group": {"_id": "$department", "headcount": {"$sum": 1}, **sums,
                    "leave_count": {"$sum": {"$ifNull": ["$metrics.leave_days", 0]}}}},
        {"$project": {
            "_id": 0, "department": "$_id", "headcount": 1, "present_days": half_up("$present_days"),
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1,
            "avg_work_hours": {"$cond": [
                {"$gt": ["$hours_count", 0]}, half_up({"$divide": ["$hours_sum", "$hours_count"]}), None,
            ]},
        }}, {"$sort": {"department": 1}},
    ]
    return aggregate_operation("employees", pipeline, "department_joined" if department is not None else "joined_department")


def leaderboard_operation(month: str, limit=10, department=None):
    first, last = month_bounds(month)
    pipeline = [
        {"$match": {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name",
                      "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ])
    return aggregate_operation("attendance_logs", pipeline, "date_employee")


def trend_operation(department: str, first: str, last: str):
    count = (Date.fromisoformat(last) - Date.fromisoformat(first)).days + 1
    if count < 1 or count > 92:
        invalid("Range must contain 1 through 92 calendar days", "to", "query")
    # $range/$map forms a bounded calendar in the database, including a department
    # with no logs anywhere in the range. $densify alone needs an anchor in that case.
    pipeline = [
        {"$match": {"department": department}},
        {"$facet": {
            "roster": [{"$group": {"_id": None, "joined": {"$push": "$joined_on"}}}],
            "counts": [
                logs_lookup(first, last, [{"$group": {
                    "_id": "$date", "present_count": {"$sum": presence_weight()},
                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                }}], "daily"),
                {"$unwind": "$daily"},
                {"$group": {"_id": "$daily._id", "present_count": {"$sum": "$daily.present_count"},
                            "late_count": {"$sum": "$daily.late_count"}}},
            ],
        }},
        {"$set": {"days": {"$map": {
            "input": {"$range": [0, count]}, "as": "offset",
            "in": {"$dateToString": {"format": "%Y-%m-%d", "timezone": "UTC", "date": {
                "$dateAdd": {"startDate": mongo_date(first), "unit": "day", "amount": "$$offset"},
            }}},
        }}, "joined": {"$ifNull": [{"$arrayElemAt": ["$roster.joined", 0]}, []]}}},
        {"$unwind": "$days"},
        {"$set": {
            "date": "$days", "is_working_day": weekday("$days"),
            "headcount": {"$size": {"$filter": {"input": "$joined", "as": "joined_on", "cond": {"$lte": ["$$joined_on", "$days"]}}}},
            "daily": {"$arrayElemAt": [{"$filter": {"input": "$counts", "as": "row", "cond": {"$eq": ["$$row._id", "$days"]}}}, 0]},
        }},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
                      "present_count": {"$ifNull": ["$daily.present_count", 0]}, "late_count": {"$ifNull": ["$daily.late_count", 0]}}},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            half_up({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4, False), None,
        ]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {
            "moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, "current"]}},
        }}},
        {"$set": {
            "present_count": half_up("$present_count"),
            "moving_avg_7d": {"$cond": [{"$ne": ["$moving_avg_7d", None]}, half_up("$moving_avg_7d", 4), None]},
            "attendance_rate": {"$toDouble": "$attendance_rate"},
        }}, {"$sort": {"date": 1}},
    ]
    return aggregate_operation("employees", pipeline, "department_joined")


def run_aggregate(operation):
    return list(db[operation["aggregate"]].aggregate(
        operation["pipeline"], hint=operation["hint"], allowDiskUse=operation["allowDiskUse"],
    ))


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: Month):
    rows = run_aggregate(monthly_operation(emp_code, month))
    if not rows:
        raise HTTPException(404, "Unknown employee")
    return public(rows[0])


@app.get("/analytics/departments/summary")
def department_summary(month: Month, department: str | None = None):
    return {"month": month, "items": public(run_aggregate(summary_operation(month, department)))}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: Month, limit: Limit = 10, department: str | None = None):
    return {"month": month, "items": public(run_aggregate(leaderboard_operation(month, limit, department)))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str, from_: Annotated[CalendarDate, Query(alias="from")],
    to: CalendarDate,
):
    operation = trend_operation(department, from_, to)
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "Unknown department")
    return {"department": department, "items": public(run_aggregate(operation))}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: ExplainEndpoint, emp_code: str | None = None, month: Month | None = None,
    department: str | None = None, limit: Limit = 10, date_from: CalendarDate | None = None,
    date_to: CalendarDate | None = None, status: Status | None = None,
    from_: Annotated[CalendarDate | None, Query(alias="from")] = None,
    to: CalendarDate | None = None, page: Page = 1, page_size: PageSize = 20,
):
    required = {
        "attendance_list": {}, "employee_monthly": {"emp_code": emp_code, "month": month},
        "department_summary": {"month": month}, "late_leaderboard": {"month": month},
        "department_trend": {"department": department, "from": from_, "to": to},
    }
    for field, value in required[endpoint].items():
        if value is None:
            invalid("Field required", field, "query")
    if endpoint == "attendance_list":
        operation = attendance_operation(emp_code, date_from, date_to, status, page, page_size)
    elif endpoint == "employee_monthly":
        operation = monthly_operation(emp_code, month)
    elif endpoint == "department_summary":
        operation = summary_operation(month, department)
    elif endpoint == "late_leaderboard":
        operation = leaderboard_operation(month, limit, department)
    else:
        operation = trend_operation(department, from_, to)
    result = db.command({"explain": operation, "verbosity": "executionStats"})
    return {"endpoint": endpoint, "collection": operation.get("aggregate", operation.get("find")), "explain": public(result)}
