# Testing, CI and Reliability

The release pipeline combines unit/component tests, integration tests and build checks. Aggregate branch coverage is not enough: high-risk modules have separate coverage floors so authentication, transactions, lifecycle, backpressure and route assembly cannot disappear inside a large overall percentage.

GitHub CI targets Python 3.10–3.14. The live integration job runs PostgreSQL 17, Redis 8 and MongoDB 8. TypeScript is checked on Node 22. Docker build and CodeQL are separate gates. Tag releases require the core jobs to pass.

v0.4.1 adds `scripts/check_manifest.py`: every tracked release file (except the manifest itself) must be represented in `MANIFEST.sha256`, every manifest path must exist, and every hash must match. This specifically prevents a repeat of the v0.4.0 legacy-files-vs-manifest drift.

Tests should include success, denial, malformed input, rollback, retry, concurrency, cleanup and resource-limit behavior—not only happy paths.
