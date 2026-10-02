from framework.security import Principal, has_permission, permission_matches


def test_permission_wildcards():
    assert permission_matches("social.*", "social.posts.read")
    assert not permission_matches("social.*", "gaming.players.read")


def test_principal_permission():
    p = Principal(kind="api_key", subject="x", roles=set(), permissions={"gaming.*"})
    assert has_permission(p, "gaming.leaderboard.list")
    assert not has_permission(p, "admin.keys.create")
