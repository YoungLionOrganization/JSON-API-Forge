# JSON API Forge Documentation

This index covers the v0.5.1 canonical documentation set. Start with `00-Start-Here.md` and progress toward production/security/reliability topics.

[![Version 0.5.1](https://img.shields.io/badge/docs-0.5.1-D4A017)](../VERSION)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../LICENSE)

## Reading paths

| Goal | Start | Continue |
|---|---|---|
| Run your first API | [Start here](00-Start-Here.md) | [CLI](22-CLI-and-Developer-Experience.md), [JSON and IDE setup](24-JSON-Schema-and-IDE-Setup.md) |
| Design an application | [Architecture](01-Architecture.md) | [Configuration](02-Configuration-Reference.md), [RPC](09-RPC-and-SQL-Operations.md) |
| Change apps during development | [Reload and isolation](44-Development-Reload-and-App-Isolation.md) | [Failure modes](26-Operational-Failure-Modes.md) |
| Connect the desktop Editor | [Control plane](42-Editor-Control-Plane.md) | [Editor branch](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/Editor) |
| Deploy and operate | [Platform matrix](43-Platform-and-Hosting-Matrix.md) | [Production checklist](14-Operations-and-Production-Checklist.md), [Observability](38-Operational-Endpoints-Metrics-and-Readiness.md) |
| Review security and rights | [Threat model](25-Security-Threat-Model.md) | [Security reporting](../SECURITY.md), [License FAQ](../LICENSE-FAQ.md), [Third-party notices](../THIRD_PARTY_NOTICES.md) |

Use the [server README](../README.md) for component selection and installation. Older migration and verification reports below describe their named releases; they do not certify the current build.

## Documents

- [00-Start-Here.md](00-Start-Here.md)
- [01-Architecture.md](01-Architecture.md)
- [02-Configuration-Reference.md](02-Configuration-Reference.md)
- [02-Multi-Project-Configuration.md](02-Multi-Project-Configuration.md)
- [03-Performance-and-Cache.md](03-Performance-and-Cache.md)
- [03-Security.md](03-Security.md)
- [04-Databases.md](04-Databases.md)
- [04-Security-and-Protection.md](04-Security-and-Protection.md)
- [05-Media.md](05-Media.md)
- [05-Plugins-and-Custom-Logic.md](05-Plugins-and-Custom-Logic.md)
- [06-Future-Features.md](06-Future-Features.md)
- [06-Messaging-Social-Gaming.md](06-Messaging-Social-Gaming.md)
- [07-Production-Checklist.md](07-Production-Checklist.md)
- [07-Production-Scaling.md](07-Production-Scaling.md)
- [08-Discord-Economy-and-PostgreSQL.md](08-Discord-Economy-and-PostgreSQL.md)
- [08-Feature-Catalog.md](08-Feature-Catalog.md)
- [09-RPC-and-SQL-Operations.md](09-RPC-and-SQL-Operations.md)
- [10-Data-Sources-and-API-Gateway.md](10-Data-Sources-and-API-Gateway.md)
- [11-FastAPI-Declarative-Features.md](11-FastAPI-Declarative-Features.md)
- [12-Client-SDK-and-Plugins.md](12-Client-SDK-and-Plugins.md)
- [13-JSON-Language-Reference.md](13-JSON-Language-Reference.md)
- [14-Operations-and-Production-Checklist.md](14-Operations-and-Production-Checklist.md)
- [15-MongoDB.md](15-MongoDB.md)
- [16-Supabase-Auth-and-PostgreSQL.md](16-Supabase-Auth-and-PostgreSQL.md)
- [17-Transactions-Idempotency-and-Consistency.md](17-Transactions-Idempotency-and-Consistency.md)
- [18-Recipes-and-Decision-Guide.md](18-Recipes-and-Decision-Guide.md)
- [19-Generated-Endpoint-Map.md](19-Generated-Endpoint-Map.md)
- [20-Upgrading-from-v0.2.md](20-Upgrading-from-v0.2.md)
- [21-v0.4-Hardening-and-Migration.md](21-v0.4-Hardening-and-Migration.md)
- [22-CLI-and-Developer-Experience.md](22-CLI-and-Developer-Experience.md)
- [23-Testing-CI-and-Reliability.md](23-Testing-CI-and-Reliability.md)
- [24-JSON-Schema-and-IDE-Setup.md](24-JSON-Schema-and-IDE-Setup.md)
- [25-Security-Threat-Model.md](25-Security-Threat-Model.md)
- [26-Operational-Failure-Modes.md](26-Operational-Failure-Modes.md)
- [27-Production-Readiness-Matrix.md](27-Production-Readiness-Matrix.md)
- [28-v0.4-Verification-Report.md](28-v0.4-Verification-Report.md)
- [29-Row-Ownership-and-Authorization.md](29-Row-Ownership-and-Authorization.md)
- [30-Database-Schema-Lifecycle-and-Migrations.md](30-Database-Schema-Lifecycle-and-Migrations.md)
- [31-Reverse-Proxy-Trust-TLS-and-Client-IP.md](31-Reverse-Proxy-Trust-TLS-and-Client-IP.md)
- [32-API-Semantics-and-HTTP-Contracts.md](32-API-Semantics-and-HTTP-Contracts.md)
- [33-Realtime-Delivery-and-Backpressure.md](33-Realtime-Delivery-and-Backpressure.md)
- [34-Senior-Engineering-Review-and-Release-Gates.md](34-Senior-Engineering-Review-and-Release-Gates.md)
- [35-Credential-Delegation-JWT-and-Operator-Trust.md](35-Credential-Delegation-JWT-and-Operator-Trust.md)
- [36-Configuration-Merge-Semantics.md](36-Configuration-Merge-Semantics.md)
- [37-Outbound-HTTP-and-Egress-Security.md](37-Outbound-HTTP-and-Egress-Security.md)
- [38-Operational-Endpoints-Metrics-and-Readiness.md](38-Operational-Endpoints-Metrics-and-Readiness.md)
- [39-Media-Consistency-Quotas-and-Batch-Semantics.md](39-Media-Consistency-Quotas-and-Batch-Semantics.md)
- [40-Rate-Limiting-Overload-and-High-RPS.md](40-Rate-Limiting-Overload-and-High-RPS.md)
- [41-Known-Limits-and-Non-Goals.md](41-Known-Limits-and-Non-Goals.md)
- [42-Editor-Control-Plane.md](42-Editor-Control-Plane.md)
- [43-Platform-and-Hosting-Matrix.md](43-Platform-and-Hosting-Matrix.md)
- [44-Development-Reload-and-App-Isolation.md](44-Development-Reload-and-App-Isolation.md)
- [cPanelGuide.md](cPanelGuide.md)

## Documentation policy

Documentation describes implemented behavior and explicit boundaries. If a doc and the generated JSON Schema/runtime disagree, treat the runtime/schema as authoritative and open a documentation defect. Unsupported future features must not be documented as implemented.
