# Operations and Production Checklist

Operate Forge as an application platform: monitor health/readiness, DB pool saturation, latency, 429/503 rates, audit queue pressure, Redis availability and storage usage. Back up both business databases and the internal metadata database according to recovery requirements.

`/health` is a shallow process health endpoint. Detailed readiness can exercise dependencies and is protected by the operator token. Metrics are process-level operational information and should not reuse a project admin credential.

During deploys: migrate first, start validate-only runtime, check readiness, shift traffic, monitor errors, and retain a rollback plan for configuration plus schema changes.
