import asyncio
import math

import httpx
import pytest

from json_api_forge import AsyncForgeClient, ForgeClient, ForgeHTTPError, RetryPolicy
from json_api_forge.client import _base_url, _headers, _relative_path
from json_api_forge.control_plane import _credential
from json_api_forge.errors import safe_error_text


@pytest.mark.parametrize(
    "value", ["%252e%252e/admin", "safe/%2Fadmin", "safe/%255cadmin", "a//b", "a/%3fquery", "a/%00", "a/%ZZ", "safe\nadmin"]
)
def test_paths_reject_encoded_traversal_and_delimiters(value):
    with pytest.raises(ValueError):
        _relative_path(value)


@pytest.mark.parametrize(
    "value", ["https://example.test/%252e%252e/base", "https://example.test:0", "https://example.test/a\t", "https://example.test:99999"]
)
def test_base_urls_reject_ambiguous_authority_or_path(value):
    with pytest.raises(ValueError):
        _base_url(value, allow_insecure_http=False)


@pytest.mark.parametrize("client_class", [ForgeClient, AsyncForgeClient])
def test_transport_bounds(client_class):
    for timeout in [math.nan, math.inf, 0, -1, 901]:
        with pytest.raises(ValueError):
            client_class("https://example.test", timeout=timeout)
    with pytest.raises(ValueError):
        client_class("https://example.test", api_key="secret\tvalue")


def test_headers_credentials_and_retry_bounds():
    for headers in [{"Host": "other.test"}, {"X-API-Key": "replacement"}, {"Bad Header": "x"}, {"X-Info": "bad\x01value"}]:
        with pytest.raises(ValueError):
            _headers("key", "request-id", None, headers)
    with pytest.raises(ValueError):
        _headers(None, "bad\nrequest", None, None)
    with pytest.raises(ValueError):
        _credential("a\tb", name="setup token")
    assert _credential("  safe  ", name="setup token") == "safe"
    for values in [{"max_attempts": 2.5}, {"backoff_seconds": math.nan}, {"max_backoff_seconds": 61}, {"jitter_ratio": math.inf}]:
        with pytest.raises(ValueError):
            RetryPolicy(**values)
    policy = RetryPolicy(backoff_seconds=2, max_backoff_seconds=2, jitter_ratio=1)
    assert 0 <= policy.delay(1, retry_after="nan") <= 2
    assert 0 <= policy.delay(1, retry_after="inf") <= 2
    assert policy.delay(1, retry_after="1000") == 2


def test_query_is_sent_but_not_exposed_to_observer_and_errors_are_redacted():
    events = []

    def server(request):
        assert request.url.path == "/base/nested/name with spaces"
        assert request.url.params["token"] == "secret"
        return httpx.Response(200, json={"ok": True})

    with ForgeClient("https://example.test/base", transport=httpx.MockTransport(server), observer=events.append) as client:
        assert client.request("GET", "nested/name%20with%20spaces?token=secret").data == {"ok": True}
    assert len(events) == 1
    assert "secret" not in str(events)
    error = ForgeHTTPError(401, {"password": "sensitive", "token": "secret", "message": "denied\nline"})
    assert "sensitive" not in str(error)
    assert "secret" not in str(error)
    assert "\n" not in str(error)
    assert error.detail["password"] == "sensitive"


def test_long_url_credentials_are_redacted_before_diagnostic_truncation():
    secret = "credential-must-stay-private" * 200
    detail = f"Upstream https://account:{secret}@example.test/path returned an error"
    rendered = safe_error_text(detail)
    assert "credential-must-stay-private" not in rendered
    assert "[redacted]@example.test/path" in rendered
    assert len(rendered) <= 4096


def test_error_text_stays_bounded_after_replacing_early_credentials():
    detail = "https://a@example.test/path " + "public detail " * 1000
    rendered = safe_error_text(detail)
    assert "https://a@" not in rendered
    assert len(rendered) <= 4096


@pytest.mark.parametrize("asynchronous", [False, True])
def test_encoded_responses_are_rejected_before_their_body_is_read(asynchronous):
    class UnreadableStream(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            raise AssertionError("Compressed body must not be consumed")

        def __aiter__(self):
            raise AssertionError("Compressed body must not be consumed")

    def server(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=UnreadableStream())

    with pytest.raises(ValueError):
        _headers(None, None, None, {"Accept-Encoding": "gzip"})
    transport = httpx.MockTransport(server)
    if asynchronous:

        async def run():
            async with AsyncForgeClient("https://example.test", transport=transport) as client:
                with pytest.raises(ForgeHTTPError, match="identity content encoding"):
                    await client.request("GET", "items")

        asyncio.run(run())
    else:
        with ForgeClient("https://example.test", transport=transport) as client:
            with pytest.raises(ForgeHTTPError, match="identity content encoding"):
                client.request("GET", "items")
