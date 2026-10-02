# Operational Endpoints, Metrics and Readiness

Forge exposes process health, readiness and optional Prometheus metrics. Health should stay cheap. Readiness may check internal DB and project services and can return a degraded/unavailable result when dependencies fail.

Detailed readiness and metrics reveal process/dependency information and are protected by the operator credential when configured. Do not reuse a project API key as the process-operator boundary.

Metrics include request counts/latency plus audit queue/drop/write-failure signals where Prometheus support is available. Monitor trends, not only whether `/health` returns 200.
