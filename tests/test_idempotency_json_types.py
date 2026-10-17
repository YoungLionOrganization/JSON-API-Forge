from decimal import Decimal

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.requests import Request

from framework import operations
from framework.config import OperationConfig, SQLStatementConfig
from framework.security import Principal


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"hello", "paylaşım 👋".encode(), b""])
async def test_sqlite_binary_replay_matches_first_wire_response(tmp_path, value):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    await operations.init_operation_idempotency(engine)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE counts (value INTEGER)"))
        await connection.execute(text("INSERT INTO counts VALUES (0)"))
    operation = OperationConfig(
        name="binary-result",
        permission="rpc",
        transaction=True,
        idempotency=True,
        statements=[
            SQLStatementConfig(sql="UPDATE counts SET value = value + 1", mode="execute"),
            SQLStatementConfig(sql="SELECT :payload AS payload", mode="fetch_one", params={"payload": value}, result_name="record"),
        ],
    )
    request = Request({"type": "http", "method": "POST", "path": "/rpc", "query_string": b"", "headers": []})
    principal = Principal("api_key", "client", set(), set())
    try:
        first, replayed = await operations.execute_idempotent_operation(
            engine, project_slug="p", operation=operation, principal=principal, raw_key="same", body={}, request=request
        )
        assert not replayed
        second, replayed = await operations.execute_idempotent_operation(
            engine, project_slug="p", operation=operation, principal=principal, raw_key="same", body={}, request=request
        )
        assert replayed
        assert jsonable_encoder(first) == jsonable_encoder(second)
        assert second["record"]["payload"] == value.decode()
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT value FROM counts"))).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [Decimal("1.25"), Decimal("123")])
async def test_decimal_driver_values_keep_json_number_type_on_replay(tmp_path, monkeypatch, value):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    await operations.init_operation_idempotency(engine)

    async def driver_result(*args, **kwargs):
        return {"balance": value}

    monkeypatch.setattr(operations, "_execute_on_connection", driver_result)
    operation = OperationConfig(
        name="decimal-result", permission="rpc", transaction=True, idempotency=True, statements=[SQLStatementConfig(sql="SELECT 1")]
    )
    request = Request({"type": "http", "method": "POST", "path": "/rpc", "query_string": b"", "headers": []})
    principal = Principal("api_key", "client", set(), set())
    try:
        first, _ = await operations.execute_idempotent_operation(
            engine, project_slug="p", operation=operation, principal=principal, raw_key="same", body={}, request=request
        )
        second, replayed = await operations.execute_idempotent_operation(
            engine, project_slug="p", operation=operation, principal=principal, raw_key="same", body={}, request=request
        )
        assert replayed
        assert jsonable_encoder(first) == second
        assert isinstance(second["balance"], (float, int))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"\xff", float("inf"), float("-inf")])
async def test_non_json_sql_results_roll_back_before_committing_replay(tmp_path, value):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    await operations.init_operation_idempotency(engine)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE counts (value INTEGER)"))
        await connection.execute(text("INSERT INTO counts VALUES (0)"))
    operation = OperationConfig(
        name="invalid-json-result",
        permission="rpc",
        transaction=True,
        idempotency=True,
        statements=[
            SQLStatementConfig(sql="UPDATE counts SET value = value + 1", mode="execute"),
            SQLStatementConfig(sql="SELECT :payload", mode="scalar", params={"payload": value}, result_name="value"),
        ],
    )
    request = Request({"type": "http", "method": "POST", "path": "/rpc", "query_string": b"", "headers": []})
    principal = Principal("api_key", "client", set(), set())
    try:
        with pytest.raises(ValueError):
            await operations.execute_idempotent_operation(
                engine, project_slug="p", operation=operation, principal=principal, raw_key="same", body={}, request=request
            )
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT value FROM counts"))).scalar_one() == 0
            assert (await connection.execute(text("SELECT COUNT(*) FROM _forge_v4_operation_idempotency"))).scalar_one() == 0
    finally:
        await engine.dispose()
