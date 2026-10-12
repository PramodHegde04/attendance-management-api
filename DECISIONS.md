# DECISIONS.md

1. **Indexes.** I created a unique index on `emp_code` to prevent duplicate employees and an index on `joined_on` for employee eligibility queries. A unique compound index on `(emp_code, date)` prevents duplicate attendance records. The `(date, emp_code)` index supports attendance listing, while `(emp_code, punch_in)` supports finding recent punch-ins. I did not add indexes for every possible filter combination because extra indexes increase storage and write costs.

2. **Punch-in race.** Both requests may initially find no attendance record. MongoDB's unique compound index allows only one insert for the same employee and date. The winning request returns `201 Created`; the other receives a duplicate-key error, which the API converts to `409 Conflict`.

3. **Ties.** The leaderboard uses a window-function rank based on total late minutes. It applies the limit after ranking, so employees tied at the cutoff are all included. Competition ranking means the next rank is skipped after a tie.

4. **Headcount.** The department summary starts from employees eligible by the period end, then combines attendance aggregates. This includes employees who have no attendance logs.

5. **One thing I would change.** For 100 times more data, I would benchmark realistic workloads, inspect query plans, and optimize aggregation pipelines and indexes based on measured bottlenecks.