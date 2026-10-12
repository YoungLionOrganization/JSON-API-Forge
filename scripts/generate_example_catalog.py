#!/usr/bin/env python3
"""Generate the large, copy-ready applications owned by the exampleApps branch."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "../../../schemas/fragment.schema.json"
SQL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def _catalog_sql(template: str, **identifiers: str) -> str:
    """Interpolate SQL identifiers sourced from the checked-in catalog only."""
    if not identifiers or any(
        SQL_IDENTIFIER.fullmatch(value) is None for value in identifiers.values()
    ):
        raise ValueError("Catalog SQL identifier is invalid")
    return template.format_map(identifiers)


EXAMPLES: tuple[dict[str, Any], ...] = (
    {
        "directory": "CommerceCore",
        "name": "Commerce Core",
        "slug": "commerce-core",
        "domain": "commerce",
        "entity": "orders",
        "statuses": ["pending", "paid", "packed", "shipped", "cancelled"],
        "focus": "Multi-stage order orchestration, ownership, monetary rollups, atomic transitions and realtime fulfilment events.",
    },
    {
        "directory": "IdentityHub",
        "name": "Identity Hub",
        "slug": "identity-hub",
        "domain": "identity",
        "entity": "profiles",
        "statuses": ["invited", "active", "locked", "archived"],
        "focus": "Identity lifecycle administration, scoped operators, policy records, assignment queues and auditable state changes.",
    },
    {
        "directory": "ProjectOps",
        "name": "Project Operations",
        "slug": "project-ops",
        "domain": "projects",
        "entity": "work_items",
        "statuses": ["backlog", "planned", "active", "blocked", "done"],
        "focus": "Portfolio work tracking with priority, assignments, workflow history, aggregate dashboards and collaboration events.",
    },
    {
        "directory": "LearningCampus",
        "name": "Learning Campus",
        "slug": "learning-campus",
        "domain": "learning",
        "entity": "enrollments",
        "statuses": ["applied", "enrolled", "paused", "completed", "withdrawn"],
        "focus": "Enrollment operations, learning workflow checkpoints, policy versioning and bounded progress reporting.",
    },
    {
        "directory": "ClinicFlow",
        "name": "Clinic Flow",
        "slug": "clinic-flow",
        "domain": "clinic",
        "entity": "care_cases",
        "statuses": ["intake", "triage", "scheduled", "in_care", "closed"],
        "focus": "Synthetic care-operations workflow with strict permissions, assignment queues and audit trails; not a medical-record system.",
    },
    {
        "directory": "FleetControl",
        "name": "Fleet Control",
        "slug": "fleet-control",
        "domain": "fleet",
        "entity": "vehicles",
        "statuses": ["available", "assigned", "maintenance", "offline"],
        "focus": "Vehicle lifecycle, dispatch ownership, maintenance workflows, cost aggregation and realtime fleet status.",
    },
    {
        "directory": "HotelOperations",
        "name": "Hotel Operations",
        "slug": "hotel-operations",
        "domain": "hotel",
        "entity": "reservations",
        "statuses": ["quoted", "confirmed", "checked_in", "checked_out", "cancelled"],
        "focus": "Reservation operations with room ownership, workflow hand-offs, revenue totals and realtime service events.",
    },
    {
        "directory": "RestaurantNetwork",
        "name": "Restaurant Network",
        "slug": "restaurant-network",
        "domain": "restaurant",
        "entity": "service_orders",
        "statuses": ["received", "preparing", "ready", "served", "void"],
        "focus": "Multi-location service order flow, preparation assignments, policy controls and live kitchen-to-service updates.",
    },
    {
        "directory": "WarehouseGrid",
        "name": "Warehouse Grid",
        "slug": "warehouse-grid",
        "domain": "warehouse",
        "entity": "inventory_batches",
        "statuses": ["received", "stored", "allocated", "picked", "depleted"],
        "focus": "Inventory batch ownership, allocation workflow, audit events, quantity totals and bounded search/filter surfaces.",
    },
    {
        "directory": "SubscriptionPlatform",
        "name": "Subscription Platform",
        "slug": "subscription-platform",
        "domain": "subscriptions",
        "entity": "accounts",
        "statuses": ["trial", "active", "past_due", "paused", "cancelled"],
        "focus": "Tenant subscription lifecycle, plan policies, usage amounts, idempotent transitions and operator dashboards.",
    },
    {
        "directory": "CreatorStudio",
        "name": "Creator Studio",
        "slug": "creator-studio",
        "domain": "creator",
        "entity": "productions",
        "statuses": ["draft", "review", "scheduled", "published", "retired"],
        "focus": "Content production operations, reviewer assignments, publishing workflow and audience-independent status analytics.",
    },
    {
        "directory": "TournamentEngine",
        "name": "Tournament Engine",
        "slug": "tournament-engine",
        "domain": "tournaments",
        "entity": "matches",
        "statuses": ["scheduled", "check_in", "live", "final", "disputed"],
        "focus": "Match lifecycle, official assignments, ruleset policies, result audit events and realtime tournament channels.",
    },
    {
        "directory": "IoTControlCenter",
        "name": "IoT Control Center",
        "slug": "iot-control-center",
        "domain": "iot",
        "entity": "devices",
        "statuses": ["provisioning", "online", "degraded", "offline", "retired"],
        "focus": "Device fleet control metadata, operator ownership, remediation workflows and realtime state notifications.",
    },
    {
        "directory": "LogisticsNetwork",
        "name": "Logistics Network",
        "slug": "logistics-network",
        "domain": "logistics",
        "entity": "shipments",
        "statuses": [
            "created",
            "in_transit",
            "at_hub",
            "out_for_delivery",
            "delivered",
            "exception",
        ],
        "focus": "Shipment orchestration, hub assignments, exception policies, idempotent updates and network-wide status totals.",
    },
    {
        "directory": "CivicPortal",
        "name": "Civic Service Portal",
        "slug": "civic-portal",
        "domain": "civic",
        "entity": "service_requests",
        "statuses": ["submitted", "accepted", "in_progress", "resolved", "rejected"],
        "focus": "Public-service back-office workflow with private mutations, case ownership, policy rules and transparent aggregate reporting.",
    },
    {
        "directory": "ResearchVault",
        "name": "Research Vault",
        "slug": "research-vault",
        "domain": "research",
        "entity": "datasets",
        "statuses": ["ingested", "curating", "reviewed", "released", "restricted"],
        "focus": "Dataset catalog operations, steward assignments, access-policy versions, provenance events and release-state analytics.",
    },
    {
        "directory": "HiringPipeline",
        "name": "Hiring Pipeline",
        "slug": "hiring-pipeline",
        "domain": "hiring",
        "entity": "candidates",
        "statuses": ["sourced", "screening", "interview", "offer", "hired", "closed"],
        "focus": "Candidate workflow metadata, recruiter ownership, structured stage transitions and privacy-minded audit events.",
    },
    {
        "directory": "IncidentCommand",
        "name": "Incident Command",
        "slug": "incident-command",
        "domain": "incidents",
        "entity": "incidents",
        "statuses": [
            "investigating",
            "identified",
            "monitoring",
            "resolved",
            "postmortem",
        ],
        "focus": "Operational incident control with responder assignments, runbook policies, immutable events and realtime command updates.",
    },
    {
        "directory": "FinanceOps",
        "name": "Finance Operations",
        "slug": "finance-ops",
        "domain": "finance",
        "entity": "batches",
        "statuses": ["draft", "review", "approved", "posted", "rejected"],
        "focus": "Synthetic financial operations workflow with scoped operator permissions, batch totals and idempotent audit transitions.",
    },
    {
        "directory": "EditorPluginRegistry",
        "name": "Editor Plugin Registry",
        "slug": "editor-plugin-registry",
        "domain": "editor",
        "entity": "plugins",
        "statuses": ["review", "approved", "deprecated", "revoked"],
        "focus": "Forge-native Editor plugin metadata, publishers, permissions, HTTPS packages and SHA-256 review state; no automatic code execution.",
        "plugin_catalog": True,
    },
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _env(slug: str) -> str:
    return slug.upper().replace("-", "_")


def _column(column_type: str, **values: Any) -> dict[str, Any]:
    return {"type": column_type, **values}


def _permissions(
    prefix: str,
    suffix: str,
    actions: tuple[str, ...] = ("list", "read", "create", "update", "delete"),
) -> dict[str, str]:
    return {action: f"{prefix}.{suffix}.{action}" for action in actions}


def _resource(
    definition: dict[str, Any],
    suffix: str,
    table_suffix: str,
    columns: dict[str, Any],
    *,
    writable: list[str],
    filters: list[str],
    sort: list[str],
    actions: list[str] | None = None,
) -> dict[str, Any]:
    domain = definition["domain"]
    resource: dict[str, Any] = {
        "database": "primary",
        "table": f"{domain}_{table_suffix}",
        "path": f"{domain}/{suffix}",
        "auto_create": True,
        "columns": columns,
        "writable_fields": writable,
        "allowed_filters": filters,
        "filter_operators": ["eq", "ne", "gt", "gte", "lt", "lte", "in", "isnull"],
        "allowed_sort": sort,
        "permissions": _permissions(
            domain,
            suffix.replace("/", "."),
            tuple(actions or ["list", "read", "create", "update", "delete"]),
        ),
    }
    if actions is not None:
        resource["allowed_actions"] = actions
    return resource


def _primary_resource(definition: dict[str, Any]) -> dict[str, Any]:
    statuses = definition["statuses"]
    columns = {
        "id": _column("integer", primary_key=True, nullable=False),
        "external_id": _column(
            "string", nullable=False, unique=True, index=True, max_length=96
        ),
        "name": _column("string", nullable=False, index=True, max_length=160),
        "status": _column(
            "string", nullable=False, default=statuses[0], index=True, max_length=32
        ),
        "owner_id": _column("string", nullable=False, index=True, max_length=96),
        "amount": _column("integer", nullable=False, default=0, index=True),
        "priority": _column("integer", nullable=False, default=0, index=True),
        "metadata": _column("json"),
        "created_at": _column("datetime", index=True),
        "updated_at": _column("datetime"),
        "deleted_at": _column("datetime", index=True),
    }
    required = ["external_id", "name", "owner_id"]
    properties: dict[str, Any] = {
        "external_id": {"type": "string", "minLength": 1, "maxLength": 96},
        "name": {"type": "string", "minLength": 1, "maxLength": 160},
        "status": {"type": "string", "enum": statuses},
        "owner_id": {"type": "string", "minLength": 1, "maxLength": 96},
        "amount": {"type": "integer", "minimum": 0, "maximum": 1_000_000_000},
        "priority": {"type": "integer", "minimum": 0, "maximum": 100},
        "metadata": {"type": "object"},
    }
    if definition.get("plugin_catalog"):
        columns.update(
            {
                "plugin_id": _column(
                    "string", nullable=False, index=True, max_length=128
                ),
                "version": _column("string", nullable=False, index=True, max_length=64),
                "publisher": _column(
                    "string", nullable=False, index=True, max_length=128
                ),
                "platform": _column(
                    "string", nullable=False, default="any", index=True, max_length=64
                ),
                "download_url": _column("string", nullable=False, max_length=1024),
                "sha256": _column("string", nullable=False, index=True, max_length=64),
                "permissions": _column("json"),
                "description": _column("text"),
            }
        )
        required.extend(["plugin_id", "version", "publisher", "download_url", "sha256"])
        properties.update(
            {
                "plugin_id": {
                    "type": "string",
                    "pattern": "^[a-z0-9]+(?:[.-][a-z0-9]+)*$",
                    "maxLength": 128,
                },
                "version": {"type": "string", "minLength": 1, "maxLength": 64},
                "publisher": {"type": "string", "minLength": 1, "maxLength": 128},
                "platform": {"type": "string", "minLength": 1, "maxLength": 64},
                "download_url": {
                    "type": "string",
                    "pattern": "^https://",
                    "maxLength": 1024,
                },
                "sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
                "permissions": {
                    "type": "array",
                    "maxItems": 32,
                    "items": {"type": "string", "maxLength": 96},
                },
                "description": {"type": "string", "maxLength": 2000},
            }
        )
    writable = list(properties)
    resource = _resource(
        definition,
        definition["entity"],
        definition["entity"],
        columns,
        writable=writable,
        filters=["status", "owner_id", "amount", "priority", "created_at"],
        sort=["id", "name", "status", "amount", "priority", "created_at"],
    )
    resource.update(
        {
            "batch_enabled": True,
            "max_batch_size": 100,
            "search_fields": ["external_id", "name"],
            "soft_delete_field": "deleted_at",
            "create_schema": {
                "type": "object",
                "required": required,
                "additionalProperties": False,
                "properties": properties,
            },
            "update_schema": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": False,
                "properties": properties,
            },
            "cache": {"enabled": True, "list_ttl_seconds": 10, "read_ttl_seconds": 30},
        }
    )
    return resource


def _resources(definition: dict[str, Any]) -> dict[str, Any]:
    workflows = _resource(
        definition,
        "workflows",
        "workflows",
        {
            "id": _column("integer", primary_key=True, nullable=False),
            "record_id": _column("integer", nullable=False, index=True),
            "step": _column("string", nullable=False, index=True, max_length=64),
            "state": _column(
                "string", nullable=False, default="open", index=True, max_length=32
            ),
            "assigned_to": _column("string", nullable=False, index=True, max_length=96),
            "due_at": _column("datetime", index=True),
            "details": _column("json"),
            "created_at": _column("datetime", index=True),
        },
        writable=[
            "record_id",
            "step",
            "state",
            "assigned_to",
            "due_at",
            "details",
            "created_at",
        ],
        filters=["record_id", "step", "state", "assigned_to", "due_at"],
        sort=["id", "state", "due_at", "created_at"],
    )
    audits = _resource(
        definition,
        "audit-events",
        "audit_events",
        {
            "id": _column("integer", primary_key=True, nullable=False),
            "record_id": _column("integer", nullable=False, index=True),
            "event_type": _column("string", nullable=False, index=True, max_length=64),
            "actor_id": _column("string", nullable=False, index=True, max_length=96),
            # SQL transition stores a status token, not an encoded JSON document.
            "payload": _column("text"),
            "idempotency_key": _column("string", index=True, max_length=128),
            "created_at": _column("datetime", index=True),
        },
        writable=[],
        filters=["record_id", "event_type", "actor_id", "created_at"],
        sort=["id", "event_type", "created_at"],
        actions=["list", "read"],
    )
    policies = _resource(
        definition,
        "policies",
        "policies",
        {
            "id": _column("integer", primary_key=True, nullable=False),
            "name": _column(
                "string", nullable=False, unique=True, index=True, max_length=128
            ),
            "enabled": _column("boolean", nullable=False, default=True, index=True),
            "version": _column("integer", nullable=False, default=1),
            "rule": _column("json", nullable=False),
            "created_at": _column("datetime", index=True),
            "updated_at": _column("datetime"),
        },
        writable=["name", "enabled", "version", "rule", "created_at", "updated_at"],
        filters=["name", "enabled", "version"],
        sort=["id", "name", "version", "created_at"],
    )
    return {
        "$schema": SCHEMA,
        "resources": [_primary_resource(definition), workflows, audits, policies],
    }


def _operations(definition: dict[str, Any]) -> dict[str, Any]:
    domain = definition["domain"]
    entity = definition["entity"]
    table = f"{domain}_{entity}"
    audit_table = f"{domain}_audit_events"
    workflow_table = f"{domain}_workflows"
    permission = f"{domain}.{entity}"
    dashboard = {
        "name": f"{domain}.dashboard",
        "method": "GET",
        "database": "primary",
        "permission": f"{permission}.analytics",
        "transaction": False,
        "statements": [
            {
                "sql": _catalog_sql(
                    "SELECT status, COUNT(*) AS records, COALESCE(SUM(amount), 0) AS total_amount, "
                    "COALESCE(AVG(priority), 0) AS average_priority FROM {table} WHERE deleted_at IS NULL "
                    "GROUP BY status ORDER BY status",
                    table=table,
                ),
                "mode": "fetch_all",
                "params": {},
                "result_name": "status_summary",
                "max_rows": 100,
            }
        ],
        "cache": {"enabled": True, "ttl_seconds": 15, "vary_by_principal": True},
        "summary": f"Bounded operational dashboard for {definition['name']}",
    }
    transition = {
        "name": f"{domain}.transition",
        "method": "POST",
        "database": "primary",
        "permission": f"{permission}.transition",
        "transaction": True,
        "idempotency": True,
        "input_schema": {
            "type": "object",
            "required": ["record_id", "next_status", "actor_id"],
            "additionalProperties": False,
            "properties": {
                "record_id": {"type": "integer", "minimum": 1},
                "next_status": {"type": "string", "enum": definition["statuses"]},
                "actor_id": {"type": "string", "minLength": 1, "maxLength": 96},
                "reason": {"type": "string", "maxLength": 500},
            },
        },
        "statements": [
            {
                "sql": _catalog_sql(
                    "UPDATE {table} SET status = :next_status, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :record_id AND deleted_at IS NULL",
                    table=table,
                ),
                "mode": "execute",
                "params": {
                    "next_status": "$body.next_status",
                    "record_id": "$body.record_id",
                },
                "require_rowcount_min": 1,
                "require_rowcount_max": 1,
                "result_name": "transition",
            },
            {
                "sql": _catalog_sql(
                    "INSERT INTO {audit_table} (record_id, event_type, actor_id, payload, idempotency_key, created_at) "
                    "VALUES (:record_id, :event_type, :actor_id, :payload, :idempotency_key, CURRENT_TIMESTAMP)",
                    audit_table=audit_table,
                ),
                "mode": "execute",
                "params": {
                    "record_id": "$body.record_id",
                    "event_type": "status.transitioned",
                    "actor_id": "$body.actor_id",
                    "payload": "$body.next_status",
                    "idempotency_key": "$header.Idempotency-Key",
                },
                "result_name": "audit",
            },
        ],
        "invalidate_resources": [f"{domain}/{entity}", f"{domain}/audit-events"],
        "invalidate_operations": [f"{domain}.dashboard"],
        "summary": "Atomic, idempotent state transition with audit insertion",
    }
    assign = {
        "name": f"{domain}.assign",
        "method": "POST",
        "database": "primary",
        "permission": f"{permission}.assign",
        "transaction": True,
        "idempotency": True,
        "input_schema": {
            "type": "object",
            "required": ["record_id", "assigned_to", "step", "due_at"],
            "additionalProperties": False,
            "properties": {
                "record_id": {"type": "integer", "minimum": 1},
                "assigned_to": {"type": "string", "minLength": 1, "maxLength": 96},
                "step": {"type": "string", "minLength": 1, "maxLength": 64},
                "due_at": {"type": ["string", "null"], "format": "date-time"},
            },
        },
        "statements": [
            {
                "sql": _catalog_sql(
                    "INSERT INTO {workflow_table} (record_id, step, state, assigned_to, due_at, created_at) "
                    "VALUES (:record_id, :step, 'open', :assigned_to, :due_at, CURRENT_TIMESTAMP)",
                    workflow_table=workflow_table,
                ),
                "mode": "execute",
                "params": {
                    "record_id": "$body.record_id",
                    "step": "$body.step",
                    "assigned_to": "$body.assigned_to",
                    "due_at": "$body.due_at",
                },
                "result_name": "assignment",
            },
            {
                "sql": _catalog_sql(
                    "UPDATE {table} SET owner_id = :assigned_to, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :record_id AND deleted_at IS NULL",
                    table=table,
                ),
                "mode": "execute",
                "params": {
                    "assigned_to": "$body.assigned_to",
                    "record_id": "$body.record_id",
                },
                "require_rowcount_min": 1,
                "require_rowcount_max": 1,
                "result_name": "owner_update",
            },
        ],
        "invalidate_resources": [f"{domain}/{entity}", f"{domain}/workflows"],
        "summary": "Atomic workflow assignment and ownership update",
    }
    return {"$schema": SCHEMA, "operations": [dashboard, transition, assign]}


def _graph(definition: dict[str, Any]) -> dict[str, Any]:
    # The graph must preserve the same validation, audit and cache contract as RPC.
    transition = _operations(definition)["operations"][1]
    nodes = [
        {
            "id": "request",
            "type": "request.input",
            "title": "Transition request",
            "x": 0,
            "y": 0,
            "properties": {
                "method": transition["method"],
                "input_schema": transition["input_schema"],
            },
        },
        {
            "id": "policy",
            "type": "auth.policy",
            "title": "Scoped authorization",
            "x": 300,
            "y": 0,
            "properties": {
                "permission": transition["permission"],
                "public": False,
            },
        },
        {
            "id": "update",
            "type": "data.mutate",
            "title": "Update active record",
            "x": 600,
            "y": 0,
            "properties": transition["statements"][0],
        },
        {
            "id": "audit",
            "type": "data.mutate",
            "title": "Record audit event",
            "x": 900,
            "y": 0,
            "properties": transition["statements"][1],
        },
        {
            "id": "operation",
            "type": "operation.call",
            "title": "Forge operation",
            "x": 1200,
            "y": 0,
            "properties": {
                key: transition[key]
                for key in (
                    "name",
                    "method",
                    "database",
                    "idempotency",
                    "summary",
                    "invalidate_resources",
                    "invalidate_operations",
                )
            },
        },
        {
            "id": "response",
            "type": "response.output",
            "title": "Operation result",
            "x": 1500,
            "y": 0,
            "properties": {"status_code": 200},
        },
    ]
    edges = [
        {
            "id": "edge-1",
            "from_node": "request",
            "from_port": "exec",
            "to_node": "policy",
            "to_port": "exec",
        },
        {
            "id": "edge-2",
            "from_node": "policy",
            "from_port": "exec",
            "to_node": "update",
            "to_port": "exec",
        },
        {
            "id": "edge-3",
            "from_node": "update",
            "from_port": "exec",
            "to_node": "audit",
            "to_port": "exec",
        },
        {
            "id": "edge-4",
            "from_node": "audit",
            "from_port": "exec",
            "to_node": "operation",
            "to_port": "exec",
        },
        {
            "id": "edge-5",
            "from_node": "operation",
            "from_port": "exec",
            "to_node": "response",
            "to_port": "exec",
        },
    ]
    return {
        "$schema": "../../../schemas/editor-graph.schema.json",
        "schema_version": 1,
        "target_document": "config/50-operations.json",
        "metadata": {
            "name": f"{definition['name']} transition",
            "compiler": "json-api-forge-editor",
        },
        "nodes": nodes,
        "edges": edges,
    }


def _documents(definition: dict[str, Any]) -> dict[str, str]:
    slug = definition["slug"]
    domain = definition["domain"]
    entity = definition["entity"]
    env = _env(slug)
    role_permissions = ["system.meta.read"]
    for suffix in (entity, "workflows", "audit-events", "policies"):
        role_permissions.extend(
            f"{domain}.{suffix}.{action}"
            for action in ("list", "read", "create", "update", "delete")
        )
    role_permissions.extend(
        [
            f"{domain}.{entity}.analytics",
            f"{domain}.{entity}.transition",
            f"{domain}.{entity}.assign",
            f"{domain}.events.publish",
            f"{domain}.events.subscribe",
        ]
    )
    app = {
        "$schema": "../../schemas/manifest.schema.json",
        "slug": slug,
        "name": definition["name"],
        "version": "1.0.0",
        "api_prefix": f"/api/{slug}/v1",
        "docs_enabled": True,
        "audit_enabled": True,
    }
    databases = {
        "$schema": SCHEMA,
        "databases": {
            "primary": {
                "url": f"$env:{env}_DATABASE_URL:-sqlite+aiosqlite:///./data/{slug}.db",
                "pool_pre_ping": True,
            }
        },
    }
    security = {
        "$schema": SCHEMA,
        "security": {
            "bootstrap_enabled": True,
            "bootstrap_admin_key": f"$env:{env}_BOOTSTRAP_ADMIN_KEY",
            "bootstrap_one_time": True,
            "allow_query_api_key": False,
            "allow_websocket_query_api_key": False,
        },
        "roles": {
            "admin": {"permissions": ["*"]},
            "operator": {"permissions": sorted(set(role_permissions))},
            "auditor": {
                "permissions": [
                    f"{domain}.{entity}.list",
                    f"{domain}.{entity}.read",
                    f"{domain}.audit-events.list",
                    f"{domain}.audit-events.read",
                    f"{domain}.{entity}.analytics",
                    "system.meta.read",
                ]
            },
        },
    }
    runtime = {
        "$schema": SCHEMA,
        "cache": {
            "enabled": True,
            "backend": "memory",
            "default_ttl_seconds": 20,
            "max_entries": 10000,
        },
        "rate_limit": {
            "enabled": True,
            "backend": "memory",
            "requests": 300,
            "window_seconds": 60,
            "burst": 75,
            "route_requests": 120,
            "route_window_seconds": 60,
            "route_burst": 30,
        },
        "protection": {
            "max_request_body_bytes": 2_097_152,
            "max_concurrent_requests": 150,
            "request_timeout_seconds": 20,
            "trusted_hosts": ["*"],
        },
        "realtime": {"backend": "memory", "redis_prefix": f"forge:{slug}"},
    }
    events = {
        "$schema": SCHEMA,
        "event_channels": [
            {
                "name": f"{slug}-updates",
                "path": f"events/{slug}-updates",
                "publish_permission": f"{domain}.events.publish",
                "subscribe_permission": f"{domain}.events.subscribe",
                "websocket_enabled": True,
                "sse_enabled": True,
                "max_message_bytes": 16384,
                "queue_size": 256,
                "max_websocket_connections": 200,
                "max_sse_connections": 200,
                "heartbeat_seconds": 15,
                "websocket_message_requests": 60,
                "websocket_message_window_seconds": 60,
                "websocket_message_burst": 15,
            }
        ],
    }
    plugin_note = (
        "\nThe `editor/plugins` resource matches the Editor's Forge catalog fields. Catalog reads never install or execute native code.\n"
        if definition.get("plugin_catalog")
        else ""
    )
    readme = f"""# {definition["name"]}

[![Forge 0.5.2](https://img.shields.io/badge/Forge-0.5.2-D4A017)](../../VERSION)
[![Example checks](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml/badge.svg?branch=exampleApps)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml?query=branch%3AexampleApps)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../../LICENSE)

{definition["focus"]}

## What this example demonstrates

| Area | Included behavior |
|---|---|
| Main records | `{domain}/{entity}` CRUD with bounded inputs, soft deletion and assignment metadata |
| Atomic imports | Up to 100 primary records per transaction, with per-row defaults |
| Supporting records | Policy, assignment and audit resources |
| Workflow | Three transactional/analytics RPCs; states: {", ".join(f"`{status}`" for status in definition["statuses"])} |
| Access | Scoped `operator` and read-only `auditor` roles, plus administration |
| Runtime | In-memory cache/rate limits and a bounded `{slug}-updates` event channel |
| Editor | An operation graph in `graphs/domain-transition.forgegraph.json` |
{plugin_note}
## Install and run

Install the [main runtime](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/INSTALL.md) at v0.5.2. From the exampleApps checkout, copy this project into that server:

```bash
bash scripts/install-example.sh {definition["directory"]} /path/to/JSON-API-Forge-main/app
```

PowerShell users can run `scripts/install-example.ps1 -Name {definition["directory"]} -Destination C:\\path\\to\\JSON-API-Forge-main\\app`. Then run these commands **from the main server root**, with its Python environment activated:

```bash
forge init
forge validate
forge doctor
forge dev
```

Open `http://127.0.0.1:8000/api/{slug}/v1/_docs`. Use the bootstrap variable declared in `config/20-security.json` to provision appropriately scoped credentials; do not publish the generated `.env` or use an administrator key in a browser. Transactional operations that require idempotency must receive an `Idempotency-Key` unique to the logical command.

## Explore and adapt

Use `POST /api/{slug}/v1/{domain}/{entity}/_batch` for up to 100 records in one transaction. The request body is an array of create payloads; the whole batch rolls back on a validation or constraint failure. This endpoint does not implement idempotent replay, so reconcile application identifiers before resubmitting after a network failure.

Read `app.json` for the API prefix, `config/40-resources.json` for allowed CRUD fields/actions, `config/50-operations.json` for SQL/RPC contracts, and `config/60-events.json` for channel permissions. Keep changes to JSON configuration validated with the main runtime.

`{domain}.assign` requires `record_id`, `assigned_to`, `step` and `due_at`; send `due_at: null` when no deadline is needed. Assignment details remain nullable JSON metadata. Transition audit `payload` stores the new status as text. Role permissions apply across this shared back-office project; the `owner_id` field is assignment metadata and does not enforce per-owner isolation.

This README and the numbered fragments are generated by `scripts/generate_example_catalog.py` in exampleApps. Change that generator when contributing catalog updates, then run it and its `--check` mode. A private deployment can adapt its own copied configuration under the project license.

## Deployment boundaries and license

Before production, review managed PostgreSQL/Redis, trusted hosts and origins, bootstrap credential retirement, role permissions, backups and migrations. In-memory coordination is process-local; realtime is best-effort. This synthetic reference is not a completed domain product or professional, medical, legal or financial advice.

See the [full catalog](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/exampleApps/EXAMPLE_APPS.md), [server documentation](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/docs/README.md), [LICENSE](../../LICENSE) and [license FAQ](../../LICENSE-FAQ.md). The example uses `LicenseRef-JAF-SASH-1.1`; copying it for permitted self-hosting does not grant alternative distribution rights.
"""
    return {
        "app.json": _json(app),
        "config/10-databases.json": _json(databases),
        "config/20-security.json": _json(security),
        "config/30-runtime.json": _json(runtime),
        "config/40-resources.json": _json(_resources(definition)),
        "config/50-operations.json": _json(_operations(definition)),
        "config/60-events.json": _json(events),
        "graphs/domain-transition.forgegraph.json": _json(_graph(definition)),
        "README.md": readme,
    }


def generate(*, check: bool) -> list[str]:
    errors: list[str] = []
    for definition in EXAMPLES:
        root = ROOT / "app" / definition["directory"]
        for relative, expected in _documents(definition).items():
            target = root / relative
            if check:
                if (
                    not target.is_file()
                    or target.read_text(encoding="utf-8") != expected
                ):
                    errors.append(str(target.relative_to(ROOT)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(expected, encoding="utf-8", newline="\n")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if generated examples are missing or stale",
    )
    args = parser.parse_args()
    errors = generate(check=args.check)
    if errors:
        raise SystemExit("Generated example catalog is stale:\n" + "\n".join(errors))
    print(
        f"OK: {len(EXAMPLES)} large example applications {'verified' if args.check else 'generated'}"
    )


if __name__ == "__main__":
    main()
