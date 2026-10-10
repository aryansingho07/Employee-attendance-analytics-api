# Design decisions

1. **Indexes.** Unique emp_code and employee/date indexes enforce identity and daily uniqueness. Department/code serves sorted employee lists; department/joined and joined/department serve headcount queries. Date/employee, status/date/employee, and employee/status/date serve attendance filters and ordering. Employee/punch_in finds the latest eligible punch. Lookups reuse the employee/date and emp_code indexes. I rejected a standalone half_day index: no endpoint filters on it.

2. **Punch-in race.** Both requests validate and find the employee, then derive the attendance date. Each attempts an insert. MongoDB's unique employee/date index admits one; that request returns 201. The other receives DuplicateKeyError, translated to 409. There is no preliminary attendance existence check.

3. **Ties.** MongoDB's $rank computes competition ranks using late-minute totals alone. Filtering rank <= limit happens afterwards, so every tied employee at the cutoff is returned. Emp_code breaks display-order ties without changing their ranks.

4. **Headcount.** The summary starts with eligible employees, then looks up grouped monthly logs. Missing logs become zero metrics; the employee still contributes one headcount. Work-hour sums and counts preserve a record-weighted average.

5. **100x data.** I would maintain indexed daily department summaries, updated after punches and corrections, while retaining source records for reconciliation. Explain statistics and measured latency would guide that change.
