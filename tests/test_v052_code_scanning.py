from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from framework.cli import _write_private_file, cmd_init, cmd_secrets
from framework.config import ProjectConfig
from framework.editor_api import DocumentWrite, EditorControlPlane, _document_path
from framework.security import api_keys_table, authenticate_request, init_security
from framework.settings import Settings


def symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and exc.winerror == 1314:
            pytest.skip("Windows account lacks symbolic-link privilege")
        raise


@pytest.mark.parametrize(
    "raw",
    [
        "../app.json",
        "/app.json",
        "config/../app.json",
        "config/./safe.json",
        "config//safe.json",
        "./app.json",
        "config/safe.json/",
        "config\\safe.json",
        "config/C:secret.json",
        "config/NUL.json",
        "config/CON .json",
        "config/COM¹.json",
        "config/LPT³.json",
        "config/safe.json.",
        "config/safe.json ",
        "config/\0bad.json",
        "config/" + "x" * 1100 + ".json",
    ],
)
def test_editor_rejects_unsafe_original_spelling(tmp_path, raw):
    with pytest.raises(HTTPException):
        _document_path(tmp_path, raw, allow_hooks=True, allow_graphs=True)


@pytest.mark.parametrize("inside", [False, True])
def test_editor_rejects_linked_document_and_control_directory(tmp_path, inside):
    project = tmp_path / "Notes"
    project.mkdir()
    other = project / "private" if inside else tmp_path / "Notes-private"
    other.mkdir()
    victim = other / "secret.json"
    victim.write_text('{"secret": true}')
    symlink(project / "config", other, directory=True)
    control = EditorControlPlane(tmp_path, Settings(_env_file=None))
    with pytest.raises(HTTPException):
        _document_path(project, "config/secret.json", allow_hooks=False, allow_graphs=False)
    with pytest.raises(HTTPException):
        control.validate_project("Notes")
    (project / "config").unlink()
    (project / "config").mkdir()
    symlink(project / "config/secret.json", victim)
    with pytest.raises(HTTPException):
        _document_path(project, "config/secret.json", allow_hooks=False, allow_graphs=False)
    assert victim.read_text() == '{"secret": true}'


@pytest.mark.parametrize("name", ["NUL", "CON", "com1", "LPT9.json", "../Other"])
def test_editor_rejects_special_project_names(tmp_path, name):
    control = EditorControlPlane(tmp_path, Settings(_env_file=None))
    with pytest.raises(HTTPException):
        control._project_dir(name, must_exist=False)


def test_editor_rejects_project_alias_for_reads_and_creation(tmp_path):
    (tmp_path / "Real").mkdir()
    symlink(tmp_path / "Alias", tmp_path / "Real", directory=True)
    control = EditorControlPlane(tmp_path, Settings(_env_file=None))
    for must_exist in (False, True):
        with pytest.raises(HTTPException):
            control._project_dir("Alias", must_exist=must_exist)


def test_editor_allows_policy_documents_and_new_projects(tmp_path):
    project = tmp_path / "Notes"
    project.mkdir()
    control = EditorControlPlane(tmp_path, Settings(_env_file=None))
    assert control._project_dir("New", must_exist=False) == tmp_path / "New"
    for raw in ("app.json", "config/safe.json", "hooks/safe.py", "graphs/safe.forgegraph.json"):
        path, normalized = _document_path(project, raw, allow_hooks=True, allow_graphs=True)
        assert path == project / raw and normalized == raw
    for raw in ("hooks/safe.py", "graphs/safe.forgegraph.json", ".env", "config/nested/safe.json"):
        with pytest.raises(HTTPException):
            _document_path(project, raw, allow_hooks=False, allow_graphs=False)


def test_staging_preserves_directory_derived_project_identity(tmp_path):
    project = tmp_path / "Notes"
    (project / "config").mkdir(parents=True)
    manifest = '{"databases":{"primary":{"url":"sqlite+aiosqlite:///notes.db"}}}'
    (project / "app.json").write_text(manifest)
    control = EditorControlPlane(tmp_path, Settings(_env_file=None, editor_read_only=False))
    content = json.dumps({"roles": {"reader": {"permissions": ["notes.read"]}}})
    asyncio.run(control.write_document("Notes", "config/new.json", DocumentWrite(content=content, expected_sha256="new")))
    assert (project / "config/new.json").read_text() == content
    assert control.validate_project("Notes")["slug"] == "notes"
    assert (project / "app.json").read_text() == manifest


def test_secret_file_private_before_publication_and_force_rotation(tmp_path, monkeypatch):
    import framework.cli as cli

    original_link = os.link
    observed = []

    def publish(source, target, **kwargs):
        observed.append((Path(source).stat().st_mode & 0o777, Path(source).read_text()))
        return original_link(source, target, **kwargs)

    monkeypatch.setattr(cli.os, "link", publish)
    args = argparse.Namespace(root=str(tmp_path), production=True, editor=False, force=False)
    cmd_init(args)
    assert observed and "OPERATOR_TOKEN=" in observed[0][1]
    if os.name != "nt":
        assert observed[0][0] == 0o600
    before = (tmp_path / ".env").read_text()
    args.force = True
    cmd_init(args)
    assert (tmp_path / ".env").read_text() != before
    if os.name != "nt":
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".forge-secret-*"))


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_init_never_modifies_linked_secret_target(tmp_path, link):
    victim = tmp_path / "victim"
    victim.write_text("KEEP=original\n")
    target = tmp_path / ".env"
    if link == "symbolic":
        symlink(target, victim)
    else:
        os.link(victim, target)
    args = argparse.Namespace(root=str(tmp_path), production=True, editor=False, force=True)
    with pytest.raises(SystemExit, match="links"):
        cmd_init(args)
    assert victim.read_text() == "KEEP=original\n"


def test_secret_export_requires_redirect_opt_in_and_never_overwrites(tmp_path, capsys):
    args = argparse.Namespace(root=str(tmp_path), count=2, output=None, stdout=False)
    with pytest.raises(SystemExit, match="redirected"):
        cmd_secrets(args)
    assert capsys.readouterr().out == ""
    args.output = "generated.txt"
    cmd_secrets(args)
    generated = (tmp_path / "generated.txt").read_text().splitlines()
    assert len(generated) == 2 and generated[0] != generated[1]
    status_output = capsys.readouterr().out
    assert all(value not in status_output for value in generated)
    with pytest.raises(SystemExit, match="overwrite"):
        cmd_secrets(args)
    args.output = None
    args.stdout = True
    cmd_secrets(args)
    assert len(capsys.readouterr().out.splitlines()) == 2
    args.count = 101
    with pytest.raises(SystemExit, match="count"):
        cmd_secrets(args)


def test_secret_publication_race_keeps_concurrently_created_file(tmp_path, monkeypatch):
    import framework.cli as cli

    target = tmp_path / "secret"
    original_link = os.link

    def concurrent_creation(source, destination, **kwargs):
        target.write_text("KEEP")
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(cli.os, "link", concurrent_creation)
    with pytest.raises(FileExistsError):
        _write_private_file(target, "NEW")
    assert target.read_text() == "KEEP"
    assert not list(tmp_path.glob(".forge-secret-*"))


def test_secret_rotation_refuses_changed_target(tmp_path, monkeypatch):
    import framework.cli as cli

    target = tmp_path / "secret"
    target.write_text("KEEP")
    original_info = cli._private_file_info
    calls = 0

    def replace_during_write(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_text("CONCURRENT")
        return original_info(path)

    monkeypatch.setattr(cli, "_private_file_info", replace_during_write)
    with pytest.raises(SystemExit, match="changed"):
        _write_private_file(target, "NEW", overwrite=True)
    assert target.read_text() == "CONCURRENT"
    assert not list(tmp_path.glob(".forge-secret-*"))


def test_existing_api_key_digest_still_authenticates(tmp_path):
    token = "jf2_" + "A" * 48
    project = ProjectConfig.model_validate(
        {
            "slug": "legacy",
            "name": "Legacy",
            "databases": {"primary": {"url": "sqlite+aiosqlite:///unused.db"}},
            "security": {"bootstrap_enabled": False, "jwt_enabled": False},
        }
    )

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        try:
            await init_security(engine)
            async with engine.begin() as connection:
                await connection.execute(
                    insert(api_keys_table).values(
                        project_slug="legacy",
                        name="pre-upgrade",
                        prefix=token[:12],
                        key_hash=hashlib.sha256(token.encode()).hexdigest(),
                        roles="",
                        permissions="legacy.read",
                        enabled=True,
                        created_at=datetime.now(timezone.utc),
                    )
                )

            def request(value):
                return Request({"type": "http", "headers": [(b"x-api-key", value.encode())], "query_string": b""})

            principal = await authenticate_request(request(token), project, engine)
            assert principal.kind == "api_key" and principal.permissions == {"legacy.read"}
            with pytest.raises(HTTPException) as rejected:
                await authenticate_request(request(token[:-1] + "B"), project, engine)
            assert rejected.value.status_code == 401
        finally:
            await engine.dispose()

    asyncio.run(run())
