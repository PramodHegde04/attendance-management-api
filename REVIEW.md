# REVIEW.md

## Defects Found

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `create_employee()` and `list_employees()` | `created_at` was returned as a datetime instead of the integer epoch milliseconds required by the API contract. | Tested `POST /employees` and `GET /employees` in Swagger. Both now return `created_at` as an integer. | Converted `created_at` with `to_epoch_ms()` in both employee endpoint responses while retaining the UTC datetime in MongoDB. |

## Items Reviewed but Not Considered Defects

- Employee listing applies the department filter to both the returned items and the total count.
- Employee and attendance listing use MongoDB filtering, sorting, and pagination.
- An unknown employee should receive `404 Not Found`.
- Duplicate employee or attendance creation should receive `409 Conflict`.
- Work hours remain null until an attendance record is punched out.
- Weekend attendance can contribute to late-minute and overtime totals without contributing to weekday present-day totals.
- Employees who meet the joining-date rule count toward department headcount even when they have no attendance logs.

##  Note

The timestamp response fix was tested using Swagger. `GET /employees` returned HTTP 200 with integer `created_at` values, and `POST /employees` returned HTTP 201 with an integer `created_at` value.

The original starter file was not available for comparison, so the timestamp mismatch is confirmed in the previously inspected implementation; it cannot be conclusively attributed to the original starter version.