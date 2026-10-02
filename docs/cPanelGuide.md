# cPanel / Passenger Deployment Guide

Forge includes `passenger_wsgi.py`, which adapts the ASGI application through `a2wsgi` for hosting environments that provide Passenger/WSGI rather than a native ASGI process. The bridge starts Forge services and JSON watching lazily on the worker’s ASGI event loop before serving its first HTTP request, then closes them at worker shutdown.

## Basic shape
1. Upload the canonical source tree to a private application directory permitted by your license.
2. Create the Python environment and install the project dependencies.
3. Create `.env` with `forge init`/strong production values and ensure it is not web-accessible.
4. Configure the Passenger application entry to `passenger_wsgi.py`.
5. Set database URLs and use external PostgreSQL/MySQL rather than relying on a local development SQLite file for concurrent production traffic.
6. Run `forge migrate` before switching production to schema validation mode.

The account-based Editor control plane can serve its HTTPS JSON endpoints through this bridge, but cPanel's WSGI mode cannot carry its WebSocket call signaling. For Editor voice/video calls, run native ASGI on an allowed port or a separate host and proxy `/__forge/editor/v1/ws/` with WebSocket upgrade support.

If Editor access is enabled, create the founder once, then set `EDITOR_SETUP_ENABLED=false`, delete `EDITOR_TOKEN`, keep `EDITOR_LEGACY_TOKEN_ENABLED=false`, restrict `EDITOR_ALLOWED_IPS`/`EDITOR_TRUSTED_HOSTS`, and apply an additional VPN or firewall policy. Do not expose the control-plane path through a public wildcard proxy.

## Important limits
Passenger/a2wsgi is primarily a compatibility path for conventional HTTP APIs. Native ASGI/Uvicorn is preferred for sustained WebSocket/SSE traffic, precise async lifecycle behavior and high concurrency. Confirm whether your cPanel provider allows long-running streaming responses, outbound network access, filesystem write paths and background tasks.

Never place `.env`, database files or local media under a publicly served document root. Configure trusted proxy CIDRs/hosts according to the provider's actual proxy topology rather than trusting forwarded headers globally.

`deploy/cpanel/.htaccess.example` is a defensive starting point, not a drop-in promise: substitute the absolute account paths and confirm the directives your hosting provider permits. Providers vary in Passenger version, Python availability, process limits and WebSocket support.
