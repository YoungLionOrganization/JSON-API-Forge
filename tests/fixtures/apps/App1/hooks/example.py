async def ping_plugin(*, request, payload, principal, app, project):
    return {
        "ok": True,
        "project": project.slug,
        "caller": principal.subject,
        "payload": payload or {},
        "request_id": request.state.request_id,
    }
