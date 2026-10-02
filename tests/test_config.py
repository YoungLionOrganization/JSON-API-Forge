from pathlib import Path

from framework.config import load_config
from framework.domain import expand_feature_packs

FIXTURE_APPS = Path(__file__).parent / "fixtures" / "apps"


def test_multi_project_loader():
    cfg = load_config(FIXTURE_APPS)
    assert {p.slug for p in cfg.projects} == {"app1", "app2"}
    app1 = next(p for p in cfg.projects if p.slug == "app1")
    assert app1.api_prefix == "/api/app1/v1"
    assert app1.cache.enabled is True
    assert any(r.path == "notes" for r in app1.resources)


def test_feature_pack_expansion():
    cfg = load_config(FIXTURE_APPS)
    app1 = next(p for p in cfg.projects if p.slug == "app1")
    expand_feature_packs(app1)
    paths = {r.path for r in app1.resources}
    assert "messaging/messages" in paths
    assert "social/posts" in paths
    assert "gaming/leaderboard" in paths


def test_feature_packs_are_safe_primitives_not_authoritative_write_backdoors():
    cfg = load_config(FIXTURE_APPS)
    app1 = next(p for p in cfg.projects if p.slug == "app1")
    expand_feature_packs(app1)
    resources = {r.path: r for r in app1.resources}
    assert resources["social/posts"].owner_field == "author_id"
    assert "author_id" not in (resources["social/posts"].writable_fields or [])
    assert resources["messaging/messages"].owner_field == "sender_id"
    assert "sender_id" not in (resources["messaging/messages"].writable_fields or [])
    for path in ("gaming/saves", "gaming/inventory", "gaming/achievements", "gaming/leaderboard", "gaming/sessions"):
        assert resources[path].allowed_actions == ["list", "read"]
    assert resources["gaming/players"].writable_fields == ["display_name"]
    assert "social.*" not in app1.roles["social_client"].permissions
    assert "gaming.*" not in app1.roles["game_client"].permissions
