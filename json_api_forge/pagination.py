from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from .errors import ForgeHTTPError
from .models import ForgeResponse, JsonObject


class Pagination:
    """Shared bounded traversal state for sync and async clients."""

    def __init__(self, params: Mapping[str, Any] | None, page_size: int, max_items: int, mode: Literal["offset", "cursor"]):
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if type(max_items) is not int or not 1 <= max_items <= 1_000_000:
            raise ValueError("max_items must be between 1 and 1000000")
        if mode not in {"offset", "cursor"}:
            raise ValueError("pagination must be offset or cursor")
        self.params = dict(params or {})
        if mode == "cursor" and {"offset", "sort"} & self.params.keys():
            raise ValueError("cursor pagination does not accept offset or sort")
        if mode == "offset" and "cursor" in self.params:
            raise ValueError("offset pagination does not accept cursor")
        offset = self.params.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        cursor = self.params.get("cursor")
        if "cursor" in self.params and not self.valid_cursor(cursor):
            raise ValueError("cursor must be a non-empty bounded string")
        self.page_size = page_size
        self.max_items = max_items
        self.mode = mode
        self.offset = offset
        self.cursor = cursor
        self.seen = {cursor} if cursor is not None else set()
        self.yielded = 0

    @staticmethod
    def valid_cursor(value: Any) -> bool:
        return isinstance(value, str) and 0 < len(value) <= 8192 and all(ord(char) >= 32 and ord(char) != 127 for char in value)

    def query(self) -> dict[str, Any]:
        query = {**self.params, "limit": min(self.page_size, self.max_items - self.yielded)}
        if self.mode == "offset":
            query["offset"] = self.offset
        elif self.cursor is not None:
            query["cursor"] = self.cursor
        return query

    def consume(self, response: ForgeResponse[Any]) -> tuple[list[JsonObject], bool]:
        def invalid(detail: str) -> None:
            raise ForgeHTTPError(response.status_code, detail, response.request_id)

        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            invalid("List response does not contain an items array")
        items = data["items"]
        limit = self.query()["limit"]
        if len(items) > limit or any(not isinstance(item, dict) for item in items):
            invalid("List response contains too many items or a non-object item")
        done = self.yielded + len(items) >= self.max_items
        if self.mode == "cursor":
            more = data.get("has_more")
            cursor = data.get("next_cursor")
            if type(more) is not bool:
                invalid("Cursor response must contain a boolean has_more")
            if more:
                if not items or not self.valid_cursor(cursor) or cursor in self.seen:
                    invalid("Cursor response did not advance to a new page")
                self.seen.add(cursor)
                self.cursor = cursor
            elif cursor is not None:
                invalid("Final cursor response must have a null next_cursor")
            done = done or not more
        else:
            self.offset += len(items)
            done = done or len(items) < limit
        self.yielded += len(items)
        return items, done


def batch_payload(payloads: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    if isinstance(payloads, (str, bytes)) or not isinstance(payloads, Sequence) or not 1 <= len(payloads) <= 5000:
        raise ValueError("batch must contain 1 to 5000 objects")
    if any(not isinstance(payload, Mapping) for payload in payloads):
        raise ValueError("batch entries must be objects")
    return [dict(payload) for payload in payloads]
