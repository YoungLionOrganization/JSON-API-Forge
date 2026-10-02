from __future__ import annotations


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def test_bootstrap_is_consumed_and_private_by_default(integration_client):
    client, admin, bootstrap, _ = integration_client
    assert client.get("/api/test/v1/admin/api-keys", headers=auth(bootstrap)).status_code == 401
    assert client.get("/api/test/v1/accounts").status_code == 401
    allowed = client.get("/api/test/v1/admin/api-keys", headers=auth(admin))
    assert allowed.status_code == 200


def test_sql_crud_cache_and_filters(integration_client):
    client, admin, _, _ = integration_client
    a = client.post("/api/test/v1/accounts", headers=auth(admin), json={"user_id": "a", "balance": 100})
    b = client.post("/api/test/v1/accounts", headers=auth(admin), json={"user_id": "b", "balance": 10})
    assert a.status_code == b.status_code == 200
    listing = client.get("/api/test/v1/accounts?balance__gte=50&sort=-balance", headers=auth(admin))
    assert [x["user_id"] for x in listing.json()["items"]] == ["a"]
    assert client.get(f"/api/test/v1/accounts/{a.json()['id']}", headers=auth(admin)).status_code == 200
    updated = client.patch(f"/api/test/v1/accounts/{a.json()['id']}", headers=auth(admin), json={"balance": 90})
    assert updated.json()["balance"] == 90


def test_atomic_idempotency_fingerprint_and_replay(integration_client):
    client, admin, _, _ = integration_client
    client.post("/api/test/v1/accounts", headers=auth(admin), json={"user_id": "from", "balance": 100})
    client.post("/api/test/v1/accounts", headers=auth(admin), json={"user_id": "to", "balance": 0})
    headers = {**auth(admin), "Idempotency-Key": "discord-interaction-1"}
    payload = {"from_user": "from", "to_user": "to", "amount": 25}
    first = client.post("/api/test/v1/rpc/transfer", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    replay = client.post("/api/test/v1/rpc/transfer", headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["_idempotent_replay"] is True
    assert replay.headers["X-Forge-Idempotent-Replay"] == "true"
    changed = client.post("/api/test/v1/rpc/transfer", headers=headers, json={**payload, "amount": 30})
    assert changed.status_code == 409
    balances = {row["user_id"]: row["balance"] for row in client.get("/api/test/v1/accounts?sort=id", headers=auth(admin)).json()["items"]}
    assert balances == {"from": 75, "to": 25}


def test_idempotent_transaction_rolls_back_on_failed_second_statement(integration_client):
    client, admin, _, _ = integration_client
    client.post("/api/test/v1/accounts", headers=auth(admin), json={"user_id": "from2", "balance": 40})
    headers = {**auth(admin), "Idempotency-Key": "rollback-1"}
    response = client.post("/api/test/v1/rpc/transfer", headers=headers, json={"from_user": "from2", "to_user": "missing", "amount": 20})
    assert response.status_code == 409
    assert client.get("/api/test/v1/accounts?user_id=from2", headers=auth(admin)).json()["items"][0]["balance"] == 40


def test_writable_json_data_source(integration_client):
    client, admin, _, _ = integration_client
    listing = client.get("/api/test/v1/catalog", headers=auth(admin))
    assert listing.json()["items"][0]["name"] == "one"
    created = client.post("/api/test/v1/catalog", headers=auth(admin), json={"name": "two"})
    item = created.json()["id"]
    assert client.patch(f"/api/test/v1/catalog/{item}", headers=auth(admin), json={"name": "two-updated"}).json()["name"] == "two-updated"
    assert client.delete(f"/api/test/v1/catalog/{item}", headers=auth(admin)).json()["deleted"] is True


def test_media_upload_meta_read_and_delete(integration_client):
    client, admin, _, _ = integration_client
    uploaded = client.post("/api/test/v1/media", headers=auth(admin), files={"file": ("hello.txt", b"hello", "text/plain")})
    assert uploaded.status_code == 200, uploaded.text
    media_id = uploaded.json()["id"]
    assert "storage_key" not in uploaded.json()
    assert client.get(f"/api/test/v1/media/{media_id}", headers=auth(admin)).content == b"hello"
    assert client.delete(f"/api/test/v1/media/{media_id}", headers=auth(admin)).json()["deleted"] is True


def test_streaming_request_body_limit_does_not_trust_content_length_only(integration_client):
    client, admin, _, _ = integration_client
    response = client.post("/api/test/v1/catalog", headers={**auth(admin), "content-type": "application/json"}, content=b"x" * 2048)
    assert response.status_code == 413


def test_explicit_public_endpoint_and_openapi_security_override(integration_client):
    client, _, _, _ = integration_client
    response = client.get("/api/test/v1/public/info")
    assert response.status_code == 200 and response.json() == {"status": "public"}
    schema = client.get("/api/test/v1/_openapi.json").json()
    assert schema["paths"]["/api/test/v1/public/info"]["get"].get("security") == []
