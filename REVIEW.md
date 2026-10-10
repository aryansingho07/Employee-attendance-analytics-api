# Review of the starter

Line numbers refer to the original candidate-kit `app/main.py`, not the replacement.

| # | Where (original function / line) | What is wrong | How to notice it | Fix |
|---|---|---|---|---|
| 1 | `compute_late_minutes`, 35 | Replaces clock fields in the punch's timezone instead of using the IST shift boundary. | On a UTC host, 04:35 UTC is 10:05 IST and should be 35 minutes late. | Build an aware IST shift start from the attendance date. |
| 2 | `compute_late_minutes`, 36–37 | Floors minutes before testing the grace boundary. | 09:40:01 against 09:30 returns 0 instead of 10. | Test elapsed seconds strictly above 600, then floor minutes. |
| 3 | `compute_late_minutes`, 35 | Uses the punch calendar date for an overnight shift start. | 05:00 IST on July 7 for a 22:00–06:00 shift belongs to July 6. | Derive the attendance date first and use it for both shift boundaries. |
| 4 | `compute_work_hours`, 41 | Float `round` does not guarantee half-up rounding. | 16,218 seconds is 4.505 hours and must become 4.51. | Divide integer seconds using Decimal and quantize with ROUND_HALF_UP. |
| 5 | `compute_overtime`, 47 | Counts overtime below the 30-minute minimum. | An 18:35 finish for an 18:30 shift should give 0, not 5. | Floor minutes and return 0 below 30. |
| 6 | `compute_overtime`, 46 | Always puts shift end on the attendance date. | July 6 22:00–06:00, finishing July 7 06:40, should give 40 minutes. | Move overnight shift end to the following day. |
| 7 | Mongo client, 22; overtime, 47 | Default BSON reads are naive while shift end is aware. | Reusing the helper on a stored punch produces a timezone subtraction error. | Configure UTC-aware BSON reads; normalize naive UTC in serialization/helpers too. |
| 8 | Models, 53–60 | Employee code, lengths, email, date, and shift formats are unconstrained. | `EMP1`, `24:00`, an invalid email, or February 30 are accepted. | Enforce the contract patterns/lengths and actual calendar validity. |
| 9 | `EmployeeIn`, 58–59 | Equal shift start/end are accepted for creation. | Create an employee with both shifts `09:30`. | Validate that the two shift times differ. |
| 10 | `PunchInIn`, 65 | Integer coercion permits seconds-looking values, numeric strings, floats, and explicit null. | Send `1783312500`, `1783312500000.0`, or a timestamp string. | Strict bounded integer milliseconds; omission alone defaults to now. |
| 11 | `PunchInIn`, 66 | Arbitrary and non-presence statuses are accepted. | Punch in with `ABSENT` or `INVALID`. | Restrict punch-in to PRESENT, WFH, and ON_DUTY. |
| 12 | `health`, 74 | Always reports ready, even when MongoDB cannot answer. | Disconnect the database and request `/health`. | Ping MongoDB and return 503 on database failure. |
| 13 | `create_employee`, 79–83 | Check-then-insert races without a unique business-key index. | Send eight simultaneous creates for one emp_code. | Unique emp_code index; insert once and translate DuplicateKeyError to 409. |
| 14 | `create_employee`, 82 | Uses naive host-local creation time. | Run on a host outside UTC; BSON interprets the naive value as UTC. | Use aware UTC server time, truncated to whole seconds. |
| 15 | Employee and attendance responses, 85, 121, 150 | Return BSON datetimes as ISO strings rather than epoch milliseconds. | Inspect created_at, punch_in, and nested audit changes. | Explicit recursive serialization to integer milliseconds. |
| 16 | `list_employees`, 93 | Page one skips the first page. | With two employees, page=1/page_size=1 returns the second. | Skip `(page - 1) * page_size`. |
| 17 | `list_employees`, 94 | Total ignores department filtering. | Filter Engineering while Sales employees exist. | Count the same filter used for the page. |
| 18 | `list_employees`, 95 | No deterministic emp_code sort. | Insert EMP0003 before EMP0001. | Sort emp_code ascending in MongoDB. |
| 19 | List endpoint parameters, 89, 130–131 | No pagination bounds. | page=0, page_size=0, or page_size=101. | Validate page >= 1 and page_size in 1–100. |
| 20 | `punch_in`, 101, 113 | Unknown employee becomes a None dereference. | Punch in using an unregistered code. | Explicit 404 employee lookup. |
| 21 | `punch_in`, 102–103 | Host timezone determines the timestamp and attendance date. | A 20:00 UTC punch belongs to the next IST day. | Convert integer milliseconds to aware UTC, then derive the IST calendar date. |
| 22 | `punch_in`, 102; duration helpers | Milliseconds are not truncated before calculation/storage. | 09:40:00.900 must store 09:40:00 and remain within grace. | Truncate every supplied instant to seconds at the boundary. |
| 23 | `punch_in`, 104–118 | Duplicate attendance check races. | Parallel punches produce multiple records for one day. | Unique `(emp_code, date)` index and direct insert with duplicate handling. |
| 24 | `punch_in`, 119; `list_attendance`, 149 | Expose a fabricated id, contrary to the natural-key contract. | Attendance responses contain `id`. | Project only contract fields; identify records by emp_code/date. |
| 25 | `list_attendance`, 127–129 | Dates/status and reversed date ranges are not validated. | February 30, unknown status, or date_from > date_to. | Validate types, calendar values, status enum, and range order with 422. |
| 26 | `list_attendance`, 144–147 | Fetches and sorts all matched documents in Python. | A small page still loads 100,000 records. | Indexed MongoDB sorting, skip, limit, and a filtered count. |
| 27 | `list_attendance`, 145 | Date ties lack the required emp_code ordering. | Multiple employees on one date appear in insertion order. | Sort by date descending, emp_code ascending. |
| 28 | `list_attendance`, 148–150 | Missing seeded half_day/history and other optional fields are not normalized. | List an uncorrected seeded record that omits these fields. | Supply contract defaults at serialization and use $ifNull in analytics. |
| 29 | Application initialization | No query indexes are created. | Explain lists/analytics against the default-index-only dataset. | Create named indexes idempotently during startup and share hints between execution and explain. |
| 30 | Optional filters, 91, 134, 142 | Truthiness treats an explicitly empty filter as absent. | `department=` returns all departments instead of the empty-string match. | Distinguish omitted parameters from provided strings using `is not None`. |

The remaining seven endpoints were missing rather than defective starter implementations;
they are now implemented and tested, including atomic punch-out and correction writes.

Examined and retained: `load_dotenv`'s default behavior already gives real environment
variables priority (made explicit with `override=False`); the 09:30/18:30 defaults match
the contract; ignoring extra/derived input fields is required; and punch-in's empty history,
null work_hours/punch_out, and zero/false derived defaults are correct. MongoDB ObjectIds
remain useful internally for conditional updates even though they are never resource identifiers.
