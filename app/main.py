"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""


import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
    )
    
    db.employees.create_index([("joined_on", ASCENDING)])

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
    )

    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)],
    )



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def to_utc_datetime(epoch_ms: int) -> datetime:
    """Convert epoch milliseconds to a naive UTC datetime."""
    return datetime.fromtimestamp(
        epoch_ms // 1000,
        tz=timezone.utc,
    ).replace(tzinfo=None)


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    """Convert a naive UTC datetime to epoch milliseconds."""
    if value is None:
        return None

    aware_utc = value.replace(tzinfo=timezone.utc)
    return int(aware_utc.timestamp() * 1000)


def truncate_to_seconds(value: datetime) -> datetime:
    """Remove microseconds before calculations and storage."""
    return value.replace(microsecond=0)


def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    """Return the IST attendance date, accounting for overnight shifts."""
    local_time = punch_in.replace(tzinfo=timezone.utc).astimezone(IST)
    start_minutes = int(shift_start[:2]) * 60 + int(shift_start[3:])
    end_minutes = int(shift_end[:2]) * 60 + int(shift_end[3:])
    punch_minutes = local_time.hour * 60 + local_time.minute

    if end_minutes <= start_minutes and punch_minutes < end_minutes:
        local_time -= timedelta(days=1)

    return local_time.date().isoformat()


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    """Calculate late minutes after the 10-minute grace period."""
    local_in = punch_in.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    hour, minute = map(int, shift_start.split(":"))

    scheduled_start = local_in.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    elapsed_seconds = int(
        (local_in - scheduled_start).total_seconds()
    )

    # For an early punch-in, there are no late minutes.
    if elapsed_seconds <= 0:
        return 0

    # No late minutes during the first 10 minutes.
    if elapsed_seconds <= 600:
        return 0

    return elapsed_seconds // 60



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """Calculate hours rounded to two decimal places, half-up."""
    seconds = int((punch_out - punch_in).total_seconds())
    hours = (Decimal(seconds) / Decimal(3600)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return float(hours)



def compute_overtime(
    punch_in: datetime,
    punch_out: datetime,
    shift_end: str,
    attendance_day: str,
    shift_start: str,
) -> int:
    """Calculate overtime after scheduled shift end, with a 30-minute minimum."""
    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_end_local = datetime.fromisoformat(attendance_day).replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )

    # Overnight shifts end on the following calendar day.
    if (end_hour, end_minute) <= (start_hour, start_minute):
        shift_end_local += timedelta(days=1)

    punch_in_local = punch_in.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    punch_out_local = punch_out.replace(
        tzinfo=timezone.utc
    ).astimezone(IST)

    # A punch-in at or after scheduled shift end cannot earn overtime
    # for that shift.
    if punch_in_local >= shift_end_local:
        return 0

    elapsed_seconds = int(
        (punch_out_local - shift_end_local).total_seconds()
    )

    # Ignore negative durations and count only complete minutes.
    overtime_minutes = max(0, elapsed_seconds // 60)

    # Overtime counts only when it reaches 30 minutes.
    if overtime_minutes < 30:
        return 0

    return overtime_minutes


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: EmailStr
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code", "name", "department")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Time must use HH:MM format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("joined_on must use YYYY-MM-DD format") from exc

        if parsed.date().isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")
        return value


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value

        if type(value) is not int or value < 100_000_000_000:
            raise ValueError(
                "punched_at must be an integer epoch timestamp in milliseconds"
            )

        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"PRESENT", "WFH", "ON_DUTY"}:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value

        if type(value) is not int or value < 100_000_000_000:
            raise ValueError(
                "punched_at must be an integer epoch timestamp in milliseconds"
            )

        return value
    
    
class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value is not None and value not in {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def validate_timestamp(cls, value):
        if value is None:
            return value
        if type(value) is not int or value < 100_000_000_000:
            raise ValueError(
                "Timestamp must be integer epoch milliseconds"
            )
        try:
            to_utc_datetime(value)
        except (ValueError, OverflowError, OSError):
            raise ValueError("Timestamp is out of range")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value):
        value = value.strip()
        if not 5 <= len(value) <= 200:
            raise ValueError("reason must contain 5 to 200 characters")
        return value

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 50:
            raise ValueError(
                "regularized_by must contain 1 to 50 characters"
            )
        return value


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB is unavailable",
        )
        


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must be different",
        )

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(doc["created_at"])
    return doc





@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {}

    if department:
        query["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(query)

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    for employee in items:
        if employee.get("created_at") is not None:
            employee["created_at"] = to_epoch_ms(employee["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

    for employee in items:
        if employee.get("created_at") is not None:
            employee["created_at"] = to_epoch_ms(employee["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Use the supplied epoch milliseconds, or the current UTC time.
    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        ts = to_utc_datetime(body.punched_at)

    # All calculations and storage use whole-second precision.
    ts = truncate_to_seconds(ts)

    # Determine the attendance date using the employee's shift rules.
    attendance_day = attendance_date(
        ts,
        emp["shift_start"],
        emp["shift_end"],
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_day,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            emp["shift_start"],
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    # Return the API timestamps as epoch milliseconds.
    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(ts)
    doc["punch_out"] = None

    return doc



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        punch_out_time = truncate_to_seconds(
            datetime.now(timezone.utc).replace(tzinfo=None)
        )
    else:
        punch_out_time = to_utc_datetime(body.punched_at)

    # Find the latest punch-in that occurred at or before punch-out.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punch_out_time},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record is already punched out",
        )

    punch_in_time = record["punch_in"]

    if punch_out_time <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if punch_out_time - punch_in_time > timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in",
        )

    work_hours = compute_work_hours(punch_in_time, punch_out_time)

    
    overtime_minutes = compute_overtime(
    punch_in_time,
    punch_out_time,
    emp["shift_end"],
    record["date"],
    emp["shift_start"],
)


    half_day = work_hours < 4.50

    # Atomic update: only one concurrent request can close this record.
    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punch_out_time,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already punched out",
        )

    updated.pop("_id", None)
    updated["punch_in"] = to_epoch_ms(updated["punch_in"])
    updated["punch_out"] = to_epoch_ms(updated["punch_out"])

    return updated




@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    def validate_date(value: Optional[str], field_name: str):
        if value is None:
            return

        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
            if parsed.date().isoformat() != value:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must use YYYY-MM-DD format",
            )

    validate_date(date_from, "date_from")
    validate_date(date_to, "date_to")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be after date_to",
        )

    if status and status not in {
        "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
    }:
        raise HTTPException(
            status_code=422,
            detail="Invalid status",
        )

    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1:
        raise HTTPException(
            status_code=422,
            detail="page_size must be at least 1",
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if status:
        query["status"] = status

    if date_from or date_to:
        query["date"] = {}

        if date_from:
            query["date"]["$gte"] = date_from

        if date_to:
            query["date"]["$lte"] = date_to

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )

    for record in docs:
        # Convert punch timestamps only when they are datetime objects.
        for field in ("punch_in", "punch_out"):
            value = record.get(field)

            if isinstance(value, datetime):
                record[field] = to_epoch_ms(value)

        # Ensure optional response fields are consistently present.
        record.setdefault("half_day", False)
        record.setdefault("history", [])

        # Normalize timestamps in the audit history.
        for entry in record["history"]:
            if isinstance(entry.get("at"), datetime):
                entry["at"] = to_epoch_ms(entry["at"])

            for field_change in entry.get("changes", {}).values():
                for key in ("from", "to"):
                    value = field_change.get(key)

                    if isinstance(value, datetime):
                        field_change[key] = to_epoch_ms(value)

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    # Validate the attendance date.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
        if parsed_date.date().isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD format",
        )

    # Verify that the employee exists.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Find the attendance record.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    # ABSENT and LEAVE records cannot contain punch timestamps.
    if body.status in {"ABSENT", "LEAVE"} and (
        body.punch_in is not None or body.punch_out is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="ABSENT or LEAVE cannot include punch timestamps",
        )

    new_status = (
        body.status
        if body.status is not None
        else record["status"]
    )

    if new_status in {"ABSENT", "LEAVE"}:
        new_punch_in = None
        new_punch_out = None
    else:
        new_punch_in = (
            to_utc_datetime(body.punch_in)
            if body.punch_in is not None
            else record.get("punch_in")
        )
        new_punch_out = (
            to_utc_datetime(body.punch_out)
            if body.punch_out is not None
            else record.get("punch_out")
        )

    # Validate punch-in requirements.
    if (
        new_status in {"PRESENT", "WFH", "ON_DUTY"}
        and new_punch_in is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in",
        )

    if new_punch_in is not None:
        expected_date = attendance_date(
            new_punch_in,
            emp["shift_start"],
            emp["shift_end"],
        )
        if expected_date != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

    # Validate punch-out requirements.
    if new_punch_out is not None:
        if new_punch_in is None or new_punch_out <= new_punch_in:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in",
            )

        if new_punch_out - new_punch_in > timedelta(hours=24):
            raise HTTPException(
                status_code=422,
                detail="Punch duration cannot exceed 24 hours",
            )

    # Recalculate derived attendance values.
    if new_punch_in is not None:
        new_late_minutes = compute_late_minutes(
            new_punch_in,
            emp["shift_start"],
        )
    else:
        new_late_minutes = 0

    if new_punch_in is not None and new_punch_out is not None:
        new_work_hours = compute_work_hours(
            new_punch_in,
            new_punch_out,
        )

        # Pass all five required arguments, including punch_in.
        new_overtime_minutes = compute_overtime(
            new_punch_in,
            new_punch_out,
            emp["shift_end"],
            date,
            emp["shift_start"],
        )

        new_half_day = new_work_hours < 4.50
    else:
        new_work_hours = None
        new_overtime_minutes = 0
        new_half_day = False

    final_values = {
        "status": new_status,
        "punch_in": new_punch_in,
        "punch_out": new_punch_out,
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day,
    }

    # Build the audit changes.
    changes = {}

    for field, new_value in final_values.items():
        old_value = record.get(field)

        if old_value != new_value:
            if field in {"punch_in", "punch_out"}:
                old_value = to_epoch_ms(old_value)
                new_value_for_history = to_epoch_ms(new_value)
            else:
                new_value_for_history = new_value

            changes[field] = {
                "from": old_value,
                "to": new_value_for_history,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )

    history_entry = {
        "at": truncate_to_seconds(
            datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Compare-and-set prevents concurrent edits from overwriting history.
    update_filter = {
        "_id": record["_id"],
        "history": record.get("history", []),
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
    }

    updated = db.attendance_logs.find_one_and_update(
        update_filter,
        {
            "$set": final_values,
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    # Normalize response timestamps.
    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            updated[field] = to_epoch_ms(value)

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = to_epoch_ms(entry["at"])

        for field_change in entry.get("changes", {}).values():
            for key in ("from", "to"):
                value = field_change.get(key)
                if isinstance(value, datetime):
                    field_change[key] = to_epoch_ms(value)

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="month must use YYYY-MM")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1, month=1
        )
    else:
        next_month = month_start.replace(month=month_start.month + 1)

    month_end = next_month - timedelta(days=1)

    employment_start = max(
        month_start,
        datetime.strptime(employee["joined_on"], "%Y-%m-%d").date(),
    )

    working_days = 0
    current_day = employment_start
    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1
        current_day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "day_of_week": {
                    "$dayOfWeek": {
                        "$dateFromString": {"dateString": "$date"}
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]},
                                ]
                            },
                            {"$cond": ["$half_day", 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"},
            }
        },
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    totals = result[0] if result else {}

    present_days = totals.get("present_days", 0)
    attendance_pct = None

    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal("100") / Decimal(working_days))
            .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
):
    # Calculate the first and last dates of the requested month.
    month_start = datetime.strptime(
        month + "-01", "%Y-%m-%d"
    ).date()

    if month_start.month == 12:
        next_month = month_start.replace(
            year=month_start.year + 1, month=1
        )
    else:
        next_month = month_start.replace(
            month=month_start.month + 1
        )

    month_end = (next_month - timedelta(days=1)).isoformat()
    month_start = month_start.isoformat()

    # Only employees who have joined by month-end count.
    employee_filter = {"joined_on": {"$lte": month_end}}

    if department is not None:
        employee_filter["department"] = department

    pipeline = [
        {"$match": employee_filter},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "joining_date": "$joined_on",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code",
                                        ]
                                    },
                                    {"$gte": ["$date", month_start]},
                                    {"$lte": ["$date", month_end]},
                                    {
                                        "$gte": [
                                            "$date",
                                            "$$joining_date",
                                        ]
                                    },
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$addFields": {
                "weekday": {
                    "$cond": [
                        {"$ne": ["$attendance.date", None]},
                        {
                            "$dayOfWeek": {
                                "$dateFromString": {
                                    "dateString": "$attendance.date"
                                }
                            }
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employees": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            "$weekday",
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$attendance.half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "avg_work_hours": {
                    "$avg": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            "$attendance.work_hours",
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$attendance.work_hours",
                            None,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$attendance.late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$attendance.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$attendance.status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$attendance.status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employees"},
                "present_days": 1,
                "avg_work_hours": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    items = list(db.employees.aggregate(pipeline))

    # Apply the required HALF_UP rounding to average work hours.
    for item in items:
        average = item["avg_work_hours"]
        if average is not None:
            item["avg_work_hours"] = float(
                Decimal(str(average)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )

    return {"month": month, "items": items}



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    month_start = month + "-01"

    year, month_number = map(int, month.split("-"))
    if month_number == 12:
        next_month = f"{year + 1}-01-01"
    else:
        next_month = f"{year}-{month_number + 1:02d}-01"

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start,
                    "$lt": next_month,
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend(
        [
            {
                "$setWindowFields": {
                    "partitionBy": (
                        "$employee.department"
                        if department is not None
                        else None
                    ),
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "_id": 1,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
):
    # Validate the dates and the maximum range.
    try:
        start_day = datetime.strptime(date_from, "%Y-%m-%d").date()
        end_day = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, detail="Dates must use YYYY-MM-DD")

    if start_day.isoformat() != date_from or end_day.isoformat() != date_to:
        raise HTTPException(422, detail="Dates must use YYYY-MM-DD")

    if end_day < start_day:
        raise HTTPException(422, detail="'to' must be on or after 'from'")

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(422, detail="Date range cannot exceed 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, detail="Department not found")

    start_datetime = datetime.combine(
        start_day, datetime.min.time()
    )
    end_datetime = datetime.combine(
        end_day + timedelta(days=1), datetime.min.time()
    )

    pipeline = [
        {
            "$documents": [
                {
                    "date_obj": start_datetime,
                    "date": start_day.isoformat(),
                }
            ]
        },
        {
            "$densify": {
                "field": "date_obj",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_datetime, end_datetime],
                },
                "partitionByFields": [],
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date_obj",
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$date", "$$day"]}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {
                        "$match": {
                            "employee.department": department
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "daily_logs",
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$department",
                                            department,
                                        ]
                                    },
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "headcount_result",
            }
        },
        {
            "$set": {
                "is_working_day": {
                    "$not": [
                        {
                            "$in": [
                                {"$dayOfWeek": "$date_obj"},
                                [1, 7],
                            ]
                        }
                    ]
                },
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0,
                    ]
                },
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$daily_logs",
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {"$eq": ["$$log.half_day", True]},
                                            0.5,
                                            1,
                                        ]
                                    },
                                    0,
                                ]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$daily_logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    "$$log.late_minutes",
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_obj": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    rows = list(db.aggregate(pipeline))

    # Round rates using decimal HALF_UP rounding.
    for row in rows:
        for field in ("attendance_rate", "moving_avg_7d"):
            value = row[field]
            if value is not None:
                row[field] = float(
                    Decimal(str(value)).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP,
                    )
                )

    return {
        "department": department,
        "from": date_from,
        "to": date_to,
        "items": rows,
    }



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(422, detail="Unsupported endpoint")

    if endpoint == "attendance_list":
        query = {}

        if emp_code is not None:
            query["emp_code"] = emp_code
        if date_from is not None:
            query["date"] = {"$gte": date_from}
        if date_to is not None:
            query.setdefault("date", {})["$lte"] = date_to
        if status is not None:
            if status not in {
                "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
            }:
                raise HTTPException(422, detail="Invalid status")
            query["status"] = status

        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, detail="date_from must not exceed date_to")

        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    
    
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="Invalid month")

        try:
            month_start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1, month=1
            )
        else:
            next_month = month_start.replace(
                month=month_start.month + 1
            )

        month_end = next_month - timedelta(days=1)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                }
            },
            {
                "$addFields": {
                    "day_of_week": {
                        "$dayOfWeek": {
                            "$dateFromString": {"dateString": "$date"}
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$in": [
                                                "$day_of_week",
                                                [2, 3, 4, 5, 6],
                                            ]
                                        },
                                    ]
                                },
                                {"$cond": ["$half_day", 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "total_overtime_minutes": {"$sum": "$overtime_minutes"},
                }
            },
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }



 
    if endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="Invalid month")

        try:
            month_start_date = datetime.strptime(
                month + "-01", "%Y-%m-%d"
            ).date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid month")

        if month_start_date.month == 12:
            next_month = month_start_date.replace(
                year=month_start_date.year + 1, month=1
            )
        else:
            next_month = month_start_date.replace(
                month=month_start_date.month + 1
            )

        month_start = month_start_date.isoformat()
        month_end = (next_month - timedelta(days=1)).isoformat()

        employee_filter = {"joined_on": {"$lte": month_end}}
        if department is not None:
            employee_filter["department"] = department

        pipeline = [
            {"$match": employee_filter},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {
                        "employee_code": "$emp_code",
                        "joining_date": "$joined_on",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$emp_code",
                                                "$$employee_code",
                                            ]
                                        },
                                        {"$gte": ["$date", month_start]},
                                        {"$lte": ["$date", month_end]},
                                        {
                                            "$gte": [
                                                "$date",
                                                "$$joining_date",
                                            ]
                                        },
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "attendance",
                }
            },
            {
                "$unwind": {
                    "path": "$attendance",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$addFields": {
                    "weekday": {
                        "$cond": [
                            {"$ne": ["$attendance.date", None]},
                            {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$attendance.date"
                                    }
                                }
                            },
                            None,
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "employees": {"$addToSet": "$emp_code"},
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$in": [
                                                "$weekday",
                                                [2, 3, 4, 5, 6],
                                            ]
                                        },
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$attendance.half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "avg_work_hours": {
                        "$avg": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$attendance.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$ne": [
                                                "$attendance.work_hours",
                                                None,
                                            ]
                                        },
                                    ]
                                },
                                "$attendance.work_hours",
                                None,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$attendance.late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {
                            "$ifNull": ["$attendance.late_minutes", 0]
                        }
                    },
                    "leave_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$attendance.status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "on_duty_count": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$attendance.status", "ON_DUTY"]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": {"$size": "$employees"},
                    "present_days": 1,
                    "avg_work_hours": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain,
        }

   
    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, detail="month is required")

        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(422, detail="Invalid month")

        start = month + "-01"
        year, month_number = map(int, month.split("-"))
        if month_number == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month_number + 1:02d}-01"

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": start,
                        "$lt": end,
                    }
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append(
                {"$match": {"employee.department": department}}
            )

        pipeline.extend(
            [
                {
                    "$setWindowFields": {
                        "partitionBy": (
                            "$employee.department"
                            if department is not None
                            else None
                        ),
                        "sortBy": {"total_late_minutes": -1},
                        "output": {"rank": {"$rank": {}}},
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "_id": 1,
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "rank": 1,
                        "emp_code": "$_id",
                        "name": "$employee.name",
                        "department": "$employee.department",
                        "total_late_minutes": 1,
                        "late_count": 1,
                    }
                },
            ]
        )

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }


   
    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(
                422, detail="department, from and to are required"
            )

        try:
            start_day = datetime.strptime(from_date, "%Y-%m-%d").date()
            end_day = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(422, detail="Dates must use YYYY-MM-DD")

        if (
            start_day.isoformat() != from_date
            or end_day.isoformat() != to_date
        ):
            raise HTTPException(422, detail="Dates must use YYYY-MM-DD")

        if end_day < start_day:
            raise HTTPException(422, detail="'from' must not exceed 'to'")

        if (end_day - start_day).days + 1 > 92:
            raise HTTPException(422, detail="Date range cannot exceed 92 days")

        if not db.employees.find_one({"department": department}):
            raise HTTPException(404, detail="Department not found")

        start_datetime = datetime.combine(
            start_day, datetime.min.time()
        )
        end_datetime = datetime.combine(
            end_day + timedelta(days=1), datetime.min.time()
        )

        pipeline = [
            {
                "$documents": [
                    {
                        "date_obj": start_datetime,
                        "date": start_day.isoformat(),
                    }
                ]
            },
            {
                "$densify": {
                    "field": "date_obj",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_datetime, end_datetime],
                    },
                    "partitionByFields": [],
                }
            },
            {
                "$set": {
                    "date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date_obj",
                        }
                    }
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"day": "$date"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$date", "$$day"]}
                            }
                        },
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "employee",
                            }
                        },
                        {"$unwind": "$employee"},
                        {
                            "$match": {
                                "employee.department": department
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                            }
                        },
                    ],
                    "as": "daily_logs",
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$date"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {
                                            "$eq": [
                                                "$department",
                                                department,
                                            ]
                                        },
                                        {"$lte": ["$joined_on", "$$day"]},
                                    ]
                                }
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_result",
                }
            },
            {
                "$set": {
                    "is_working_day": {
                        "$not": [
                            {
                                "$in": [
                                    {"$dayOfWeek": "$date_obj"},
                                    [1, 7],
                                ]
                            }
                        ]
                    },
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_result.count", 0]},
                            0,
                        ]
                    },
                    "present_count": {
                        "$sum": {
                            "$map": {
                                "input": "$daily_logs",
                                "as": "log",
                                "in": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$log.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$$log.half_day", True]},
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                },
                            }
                        }
                    },
                    "late_count": {
                        "$size": {
                            "$filter": {
                                "input": "$daily_logs",
                                "as": "log",
                                "cond": {
                                    "$gt": ["$$log.late_minutes", 0]
                                },
                            }
                        }
                    },
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {"$divide": ["$present_count", "$headcount"]},
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date_obj": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": 1,
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1,
                }
            },
            {"$sort": {"date": 1}},
        ]

        
        explain = db.command(
            "explain",
            {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
