from pathlib import Path

import pytest
from starlette.requests import Request

from framework.config import load_config
from framework.factory import create_app
from framework.operations import resolve_value
from framework.security import Principal

FIXTURE_APPS = Path(__file__).parent / "fixtures" / "apps"


@pytest.fixture(autouse=True)
def _force_development_settings(monkeypatch):
    # CI exercises forge init --production before pytest. Keep these route
    # contract tests independent from the generated checkout-local .env file.
    monkeypatch.setattr("framework.factory.settings.app_env", "development")


def test_v03_declarative_features_are_loaded():
    cfg = load_config(FIXTURE_APPS)
    app1 = next(p for p in cfg.projects if p.slug == "app1")
    assert {o.name for o in app1.operations} >= {"economy.balance", "economy.transfer", "economy.grant"}
    assert any(d.name == "catalog-file" for d in app1.data_sources)
    assert any(e.name == "notifications" for e in app1.event_channels)


def test_parameter_sources():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x/7",
        "headers": [(b"x-test", b"hello")],
        "query_string": b"page=3",
        "path_params": {"user_id": "7"},
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }
    request = Request(scope)
    request.state.request_id = "req-1"
    request.state.validated_parameters = {"page": 3}
    p = Principal(kind="api_key", subject="bot", roles=set(), permissions=set(), tenant_id="guild-1")
    body = {"amount": 42, "user": {"id": "abc"}}
    assert resolve_value("$body.user.id", body=body, request=request, principal=p) == "abc"
    assert resolve_value("$param.page", body=body, request=request, principal=p) == 3
    assert resolve_value("$query.page", body=body, request=request, principal=p) == "3"
    assert resolve_value("$path.user_id", body=body, request=request, principal=p) == "7"
    assert resolve_value("$header.x-test", body=body, request=request, principal=p) == "hello"
    assert resolve_value("$principal.tenant_id", body=body, request=request, principal=p) == "guild-1"
    assert resolve_value("$$literal", body=body, request=request, principal=p) == "$literal"


def test_v03_routes_exist_without_startup():
    paths = {getattr(r, "path", "") for r in create_app(apps_dir=FIXTURE_APPS).routes}
    assert "/api/app1/v1/rpc/economy.transfer" in paths
    assert "/api/app1/v1/content/catalog" in paths
    assert "/api/app1/v1/events/notifications/stream" in paths
    assert "/api/app1/v1/events/notifications/ws" in paths
    assert "/api/app1/v1/_docs" in paths


def test_external_jwks_security_config_validation():
    from framework.config import SecurityConfig

    cfg = SecurityConfig(jwt_provider="jwks", jwt_jwks_url="https://example.invalid/.well-known/jwks.json")
    assert cfg.jwt_provider == "jwks"
    assert "RS256" in cfg.jwt_algorithms


def test_dynamic_internal_bindings_never_become_public_query_parameters():
    schema = create_app(apps_dir=FIXTURE_APPS).openapi()
    for path, method in [
        ("/api/app1/v1/notes", "get"),
        ("/api/app1/v1/plugin/ping", "post"),
        ("/api/app1/v1/rpc/economy.transfer", "post"),
        ("/api/app1/v1/admin/api-keys", "post"),
    ]:
        assert not any(p["name"].startswith("_") for p in schema["paths"][path][method].get("parameters", []))


def test_patch_and_put_have_distinct_operation_ids():
    route = create_app(apps_dir=FIXTURE_APPS).openapi()["paths"]["/api/app1/v1/notes/{item_id}"]
    assert route["patch"]["operationId"] != route["put"]["operationId"]
