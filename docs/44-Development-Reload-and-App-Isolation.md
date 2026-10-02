# Application reload and failure isolation

Version 0.5.1 watches declarative app configuration without closing the ASGI listener.
The source entrypoint (`main:app`), `forge dev`, and the standalone server use
`framework.reload:create_live_app`. Embedded callers can still use
`framework.factory.create_app` for a fixed configuration snapshot.

## JSON updates

Adding, editing, disabling or removing an app manifest, or changing numbered
`config/*.json` fragments, schedules a debounced reload. App environment values
are re-read from the deployment root `.env`; process-wide settings and Python
hooks still require a worker restart. `forge dev --reload` enables the separate
Uvicorn Python-hook reloader. `--no-reload` disables that Python reloader.

`APPS_AUTO_RELOAD=false` disables JSON watching. The default polling interval is
one second (`APPS_RELOAD_INTERVAL_SECONDS`), followed by a stable half-second
window (`APPS_RELOAD_DEBOUNCE_SECONDS`). Files under data/media and database files
are not watched. JSON configuration is limited to 2 MiB per file and 256 fragments
per app. Publish configuration files atomically to avoid intermediate states.

A candidate route table and runtime are built before one atomic switch. Unchanged
apps keep their existing services. Active requests, streams and WebSockets retain
their original generation until completion. At most two old generations may drain;
further changes wait while long-lived streams keep both generations active.

Malformed edits and runtime startup failures retain the affected app's last valid
configuration. Other valid changes can proceed. On initial startup every app
participating in an overlapping API prefix or duplicate slug is quarantined;
no directory ordering decides which app receives another app's traffic. Reload
rejects ownership conflicts that cannot safely retain the current identities.
Duplicate generated routes and operation IDs are rejected before publication.

## Health and deployment

`/health` is process liveness. `/ready` returns 503 when an app is unavailable,
configuration is malformed, or there are no active apps. Operator-authorized
readiness includes app names and exception types, never configuration secrets.
`forge validate` and `forge doctor` remain strict deployment checks.

Global security configuration and the internal identity database remain shared
dependencies. Native crashes, process exits and untrusted Python hooks require
separate OS processes or containers for isolation. App directories and storage
parents must be controlled by trusted operators. For multiple ASGI workers, each
worker independently observes files; use a restart or deployment rollout when a
change must become active across all workers simultaneously.

Data writes and media uploads publish complete files atomically. On platforms
supporting descriptor-relative paths, filesystem operations reject symlink parents
during traversal. A configuration reload does not make arbitrary Python safe.
