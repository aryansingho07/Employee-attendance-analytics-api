# Implementation plan

The deliverable is a FastAPI service that follows the supplied OpenAPI contract,
with MongoDB 6.0 or newer as its database. All application code belongs in
`app/main.py`. The source candidate kit remains unchanged.

1. Read the problem statement, OpenAPI descriptions, data model, and starter.
   Record concrete starter defects with failing examples in `REVIEW.md`.
2. Implement strict boundary validation, UTC BSON datetime storage, IST shift
   calendars, whole-second truncation, and Decimal half-up rounding.
3. Implement employee creation and filtered, ordered database pagination.
   Create unique business-key indexes at startup; translate duplicate writes
   into 409 responses.
4. Implement punch-out using the most recent eligible punch-in, including
   overnight shifts. Use atomic conditional writes to prevent double closure.
5. Implement corrections with final-state validation, recalculated metrics,
   an append-only audit entry, and conflict detection against a read snapshot.
6. Implement monthly and department aggregates, competition ranking with ties,
   and a daily calendar generated in MongoDB with a seven-row moving average.
   Preserve employees without logs and the contract's weekend semantics.
7. Reuse operation builders for live queries and executionStats explain output.
   Verify the actual plans and lookup indexes on 100,000 generated records.
8. Test edge cases, concurrency, response shapes, and readiness on a real MongoDB
   instance. Finish `DECISIONS.md` and concise setup instructions.

Acceptance includes every contract endpoint, no leaked database identifiers,
no Python document loops for analytics, no lost correction history, and no
committed secrets, database dumps, Dockerfile, or runtime dependencies.

## Completion

All eight implementation steps are complete. The real-MongoDB rule/concurrency
tests, supplied response-schema checks, independent 100,000-record analytics
comparisons, explain-plan checks, and real Uvicorn readiness check passed.
Observed results and reproducible commands are in `VERIFICATION.md`.
The implementation is ready for review in the GitHub repository. Submission
of its URL to the hiring team is a separate step.
