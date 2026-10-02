# Recipes and Decision Guide

Use a SQL resource when you need conventional bounded CRUD. Use an operation when a domain action spans multiple statements, requires a transaction/row-count guards or has idempotency semantics. Use a data source for controlled external/file/static reads. Use a custom hook/endpoint when business behavior is easier to understand in Python.

Use memory backends for one-process development or explicitly local semantics. Use Redis when limits/cache/events must coordinate between workers. Use local media only when the filesystem is appropriately shared for the deployment topology.

Prefer explicit permissions over broad wildcard roles; prefer server-controlled tenant/owner fields; prefer an explicit migration before production startup; prefer a small hook over a giant generic config expression.
