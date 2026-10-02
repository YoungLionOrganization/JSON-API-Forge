# Row Ownership and Authorization

Tenant isolation and row ownership are separate constraints and can be combined. Tenant fields bind rows to a principal tenant; owner fields bind configured actions to the principal subject unless an explicit bypass permission is held.

Policy fields are not client-writable. Create injects them; update/replace preserves them; list/read/update/delete include policy filters. Soft-delete fields are likewise server controlled.

Cache keys must include tenant/owner context when those constraints affect visibility. A broad endpoint permission does not bypass row ownership unless the configured owner bypass permission is present.
