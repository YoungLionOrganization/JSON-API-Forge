# Upgrading from v0.2

v0.3 added operations, data sources, realtime, MongoDB/JWKS and broader endpoint configuration; v0.4 hardened defaults and boundaries. Upgrade through the current config model rather than copying permissive old examples unchanged.

Run strict validation, fix unknown keys, move secrets to environment/init output, explicitly mark public surfaces, review permission strings, migrate support schemas, and test behavior under the current response/authorization semantics. Read the v0.4 migration document next.
