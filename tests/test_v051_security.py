import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from framework.config import CustomEndpointConfig, DataSourceConfig, ProjectConfig, RequestParameterSpec, ResponseSpec, SecurityConfig
from framework.editor_call_client import call_client_page, parse_ice_servers
from framework.validation import validate_json_schema


def _project(**values):
    return ProjectConfig.model_validate(
        {
            "slug": "secure",
            "name": "Secure project",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///:memory:"}},
            **values,
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://keys.example.test/jwks.json",
        "https://user:secret@keys.example.test/jwks.json",
        "https://keys.example.test/jwks.json#fragment",
        "https://keys.example.test/jwks.json\nX-Test: injected",
    ],
)
def test_jwks_urls_fail_closed(url):
    with pytest.raises(ValidationError):
        SecurityConfig(jwt_provider="jwks", jwt_jwks_url=url)


def test_jwks_private_and_plain_http_require_explicit_policy():
    cfg = SecurityConfig(
        jwt_provider="jwks",
        jwt_jwks_url="http://127.0.0.1:8080/jwks.json",
        jwks_allow_insecure_http=True,
        jwks_allow_private_networks=True,
    )
    assert cfg.jwks_allow_insecure_http is True
    assert cfg.jwks_allow_private_networks is True
    assert cfg.jwks_max_response_bytes == 1024 * 1024


def test_http_datasource_and_response_metadata_reject_header_injection():
    with pytest.raises(ValidationError):
        DataSourceConfig(
            name="upstream",
            type="http",
            url="https://example.test/api",
            public=True,
            headers={"X-Test": "safe\r\nX-Injected: yes"},
        )
    with pytest.raises(ValidationError):
        ResponseSpec(headers={"Bad Header": "value"})
    with pytest.raises(ValidationError):
        ResponseSpec(filename="report.txt\r\nX-Injected: yes")


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test/path",
        "https://user@example.test",
        "https://example.test?query=yes",
        "https://example.test\nX-Injected: yes",
    ],
)
def test_cors_origins_are_exact_origins(origin):
    with pytest.raises(ValidationError):
        _project(cors_origins=[origin])


def test_request_and_json_schema_patterns_use_bounded_linear_regex():
    bounded = RequestParameterSpec(name="query", pattern="(a+)+$")
    assert bounded.max_length == 16_384
    with pytest.raises(ValidationError):
        RequestParameterSpec(name="query", pattern="(?=unsafe)")
    with pytest.raises(ValidationError):
        RequestParameterSpec(name="query", pattern=r"(a)\1")
    with pytest.raises(HTTPException):
        validate_json_schema("a" * 16_000 + "!", {"type": "string", "pattern": "(a+)+$"})


def test_pattern_properties_and_additional_properties_remain_supported():
    schema = {
        "type": "object",
        "patternProperties": {"^safe_[a-z]+$": {"type": "integer"}},
        "additionalProperties": False,
    }
    validate_json_schema({"safe_value": 1}, schema)
    with pytest.raises(HTTPException):
        validate_json_schema({"unsafe": 1}, schema)


def test_pattern_properties_cannot_reenter_stdlib_regex_through_unevaluated_properties():
    with pytest.raises(ValidationError):
        CustomEndpointConfig(
            path="schema-test",
            handler="hooks.example:handler",
            public=True,
            input_schema={
                "allOf": [{"patternProperties": {"(a+)+$": {"type": "string"}}}],
                "unevaluatedProperties": False,
            },
        )


def test_call_client_declares_mode_before_use_and_rejects_ice_controls():
    page, _nonce = call_client_page()
    declaration = "let socket=null, localStream=null, iceServers=[], mode='audio';"
    assert page.index(declaration) < page.index("mode==='video'")
    with pytest.raises(RuntimeError):
        parse_ice_servers('[{"urls":"stun:example.test\\nturn:internal.test"}]')
    with pytest.raises(RuntimeError):
        parse_ice_servers('[{"urls":"turns:example.test","credential":"secret\\r\\ninjected"}]')
