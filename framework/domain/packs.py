from __future__ import annotations

from copy import deepcopy

from ..config import ColumnConfig, ProjectConfig, ResourceConfig


def _c(type_: str = "string", **kwargs) -> ColumnConfig:
    return ColumnConfig(type=type_, **kwargs)


def _resource(
    *,
    db: str,
    table: str,
    path: str,
    columns: dict[str, ColumnConfig],
    tenant_field: str | None = None,
    filters: list[str] | None = None,
    sort: list[str] | None = None,
    writable: list[str] | None = None,
    soft_delete: str | None = None,
    pagination_mode: str = "offset",
    cursor_field: str | None = None,
    owner_field: str | None = None,
    owner_actions: list[str] | None = None,
    allowed_actions: list[str] | None = None,
) -> ResourceConfig:
    return ResourceConfig(
        database=db,
        table=table,
        path=path,
        auto_create=True,
        columns=columns,
        tenant_field=tenant_field,
        allowed_filters=filters or [],
        allowed_sort=sort or ["id"],
        writable_fields=writable,
        soft_delete_field=soft_delete,
        owner_field=owner_field,
        owner_actions=owner_actions or [],
        owner_bypass_permission=f"{path.replace(chr(47), chr(46))}.owner_bypass" if owner_field else None,
        allowed_actions=allowed_actions or ["list", "read", "create", "update", "delete"],
        pagination_mode=pagination_mode,
        cursor_field=cursor_field,
        permissions={
            "list": f"{path.replace(chr(47), chr(46))}.list",
            "read": f"{path.replace(chr(47), chr(46))}.read",
            "create": f"{path.replace(chr(47), chr(46))}.create",
            "update": f"{path.replace(chr(47), chr(46))}.update",
            "delete": f"{path.replace(chr(47), chr(46))}.delete",
        },
    )


def _with_tenant(columns: dict[str, ColumnConfig], tenant_field: str | None) -> dict[str, ColumnConfig]:
    out = deepcopy(columns)
    if tenant_field and tenant_field not in out:
        out[tenant_field] = _c("string", nullable=False, index=True, max_length=96)
    return out


def messaging_resources(project: ProjectConfig) -> list[ResourceConfig]:
    spec = project.features.messaging
    if not spec.enabled:
        return []
    p, db, tenant = spec.table_prefix, spec.database, spec.tenant_field
    return [
        _resource(
            db=db,
            table=p + "conversations",
            path="messaging/conversations",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "type": _c("string", nullable=False, max_length=32, index=True),
                    "title": _c("string", max_length=160),
                    "created_by": _c("string", nullable=False, index=True),
                    "created_at": _c("datetime", nullable=False),
                    "updated_at": _c("datetime"),
                    "deleted_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["type", "created_by"],
            sort=["id", "created_at", "updated_at"],
            soft_delete="deleted_at",
            writable=["type", "title"],
            owner_field="created_by",
            owner_actions=["list", "read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "members",
            path="messaging/members",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "conversation_id": _c("integer", nullable=False, index=True),
                    "user_id": _c("string", nullable=False, index=True),
                    "role": _c("string", nullable=False, max_length=32),
                    "joined_at": _c("datetime", nullable=False),
                    "muted_until": _c("datetime"),
                    "last_read_message_id": _c("integer"),
                },
                tenant,
            ),
            filters=["conversation_id", "user_id", "role"],
            sort=["id", "joined_at"],
            writable=["conversation_id", "muted_until", "last_read_message_id"],
            owner_field="user_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "messages",
            path="messaging/messages",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "conversation_id": _c("integer", nullable=False, index=True),
                    "sender_id": _c("string", nullable=False, index=True),
                    "kind": _c("string", nullable=False, max_length=32, index=True),
                    "content": _c("text"),
                    "media_id": _c("string", index=True),
                    "reply_to_id": _c("integer", index=True),
                    "metadata": _c("json"),
                    "created_at": _c("datetime", nullable=False, index=True),
                    "edited_at": _c("datetime"),
                    "deleted_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["conversation_id", "sender_id", "kind"],
            sort=["id", "created_at"],
            soft_delete="deleted_at",
            pagination_mode="cursor",
            cursor_field="id",
            writable=["conversation_id", "kind", "content", "media_id", "reply_to_id", "metadata"],
            owner_field="sender_id",
            owner_actions=["update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "reactions",
            path="messaging/reactions",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "message_id": _c("integer", nullable=False, index=True),
                    "user_id": _c("string", nullable=False, index=True),
                    "emoji": _c("string", nullable=False, max_length=32),
                    "created_at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["message_id", "user_id", "emoji"],
            writable=["message_id", "emoji"],
            owner_field="user_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "receipts",
            path="messaging/receipts",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "message_id": _c("integer", nullable=False, index=True),
                    "user_id": _c("string", nullable=False, index=True),
                    "status": _c("string", nullable=False, max_length=24, index=True),
                    "at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["message_id", "user_id", "status"],
            writable=["message_id", "status", "at"],
            owner_field="user_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
    ]


def social_resources(project: ProjectConfig) -> list[ResourceConfig]:
    spec = project.features.social
    if not spec.enabled:
        return []
    p, db, tenant = spec.table_prefix, spec.database, spec.tenant_field
    return [
        _resource(
            db=db,
            table=p + "profiles",
            path="social/profiles",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "user_id": _c("string", nullable=False, unique=True, index=True),
                    "username": _c("string", nullable=False, unique=True, index=True, max_length=64),
                    "display_name": _c("string", max_length=120),
                    "bio": _c("text"),
                    "avatar_media_id": _c("string"),
                    "metadata": _c("json"),
                    "created_at": _c("datetime", nullable=False),
                    "updated_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["user_id", "username"],
            sort=["id", "username", "created_at"],
            writable=["username", "display_name", "bio", "avatar_media_id", "metadata"],
            owner_field="user_id",
            owner_actions=["read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "posts",
            path="social/posts",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "author_id": _c("string", nullable=False, index=True),
                    "text": _c("text"),
                    "visibility": _c("string", nullable=False, index=True, max_length=24),
                    "media": _c("json"),
                    "metadata": _c("json"),
                    "created_at": _c("datetime", nullable=False, index=True),
                    "updated_at": _c("datetime"),
                    "deleted_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["author_id", "visibility"],
            sort=["id", "created_at", "updated_at"],
            soft_delete="deleted_at",
            pagination_mode="cursor",
            cursor_field="id",
            writable=["text", "visibility", "media", "metadata"],
            owner_field="author_id",
            owner_actions=["read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "comments",
            path="social/comments",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "post_id": _c("integer", nullable=False, index=True),
                    "author_id": _c("string", nullable=False, index=True),
                    "parent_id": _c("integer", index=True),
                    "text": _c("text", nullable=False),
                    "created_at": _c("datetime", nullable=False, index=True),
                    "updated_at": _c("datetime"),
                    "deleted_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["post_id", "author_id", "parent_id"],
            sort=["id", "created_at"],
            soft_delete="deleted_at",
            writable=["post_id", "parent_id", "text"],
            owner_field="author_id",
            owner_actions=["read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "reactions",
            path="social/reactions",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "target_type": _c("string", nullable=False, index=True, max_length=24),
                    "target_id": _c("integer", nullable=False, index=True),
                    "user_id": _c("string", nullable=False, index=True),
                    "reaction": _c("string", nullable=False, index=True, max_length=32),
                    "created_at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["target_type", "target_id", "user_id", "reaction"],
            writable=["target_type", "target_id", "reaction"],
            owner_field="user_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "follows",
            path="social/follows",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "follower_id": _c("string", nullable=False, index=True),
                    "followee_id": _c("string", nullable=False, index=True),
                    "status": _c("string", nullable=False, index=True, max_length=24),
                    "created_at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["follower_id", "followee_id", "status"],
            writable=["followee_id", "status"],
            owner_field="follower_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
        _resource(
            db=db,
            table=p + "notifications",
            path="social/notifications",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "user_id": _c("string", nullable=False, index=True),
                    "kind": _c("string", nullable=False, index=True, max_length=48),
                    "actor_id": _c("string", index=True),
                    "payload": _c("json"),
                    "read_at": _c("datetime"),
                    "created_at": _c("datetime", nullable=False, index=True),
                },
                tenant,
            ),
            filters=["user_id", "kind"],
            sort=["id", "created_at", "read_at"],
            writable=["read_at"],
            owner_field="user_id",
            owner_actions=["list", "read", "update", "delete"],
        ),
    ]


def gaming_resources(project: ProjectConfig) -> list[ResourceConfig]:
    spec = project.features.gaming
    if not spec.enabled:
        return []
    p, db, tenant = spec.table_prefix, spec.database, spec.tenant_field
    return [
        _resource(
            db=db,
            table=p + "players",
            path="gaming/players",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "user_id": _c("string", nullable=False, unique=True, index=True),
                    "display_name": _c("string", max_length=96),
                    "level": _c("integer", nullable=False, default=1, index=True),
                    "xp": _c("integer", nullable=False, default=0),
                    "currency": _c("integer", nullable=False, default=0),
                    "stats": _c("json"),
                    "created_at": _c("datetime", nullable=False),
                    "updated_at": _c("datetime"),
                },
                tenant,
            ),
            filters=["user_id", "level"],
            sort=["id", "level", "xp", "currency"],
            writable=["display_name"],
            owner_field="user_id",
            owner_actions=["list", "read", "update"],
        ),
        _resource(
            db=db,
            table=p + "saves",
            path="gaming/saves",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "player_id": _c("integer", nullable=False, index=True),
                    "slot": _c("string", nullable=False, index=True, max_length=48),
                    "revision": _c("integer", nullable=False, default=1),
                    "data": _c("json", nullable=False),
                    "checksum": _c("string", max_length=128),
                    "updated_at": _c("datetime", nullable=False, index=True),
                },
                tenant,
            ),
            filters=["player_id", "slot"],
            sort=["id", "updated_at", "revision"],
            allowed_actions=["list", "read"],
        ),
        _resource(
            db=db,
            table=p + "inventory",
            path="gaming/inventory",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "player_id": _c("integer", nullable=False, index=True),
                    "item_key": _c("string", nullable=False, index=True, max_length=96),
                    "quantity": _c("integer", nullable=False, default=1),
                    "properties": _c("json"),
                    "updated_at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["player_id", "item_key"],
            sort=["id", "quantity", "updated_at"],
            allowed_actions=["list", "read"],
        ),
        _resource(
            db=db,
            table=p + "achievements",
            path="gaming/achievements",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "player_id": _c("integer", nullable=False, index=True),
                    "achievement_key": _c("string", nullable=False, index=True, max_length=96),
                    "progress": _c("float", nullable=False, default=0),
                    "unlocked_at": _c("datetime"),
                    "metadata": _c("json"),
                },
                tenant,
            ),
            filters=["player_id", "achievement_key"],
            allowed_actions=["list", "read"],
        ),
        _resource(
            db=db,
            table=p + "leaderboard",
            path="gaming/leaderboard",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "board_key": _c("string", nullable=False, index=True, max_length=64),
                    "player_id": _c("integer", nullable=False, index=True),
                    "score": _c("float", nullable=False, index=True),
                    "season": _c("string", index=True, max_length=64),
                    "updated_at": _c("datetime", nullable=False),
                },
                tenant,
            ),
            filters=["board_key", "player_id", "season"],
            sort=["score", "updated_at", "id"],
            allowed_actions=["list", "read"],
        ),
        _resource(
            db=db,
            table=p + "sessions",
            path="gaming/sessions",
            tenant_field=tenant,
            columns=_with_tenant(
                {
                    "id": _c("integer", primary_key=True, nullable=False),
                    "player_id": _c("integer", nullable=False, index=True),
                    "server_id": _c("string", index=True, max_length=96),
                    "started_at": _c("datetime", nullable=False, index=True),
                    "ended_at": _c("datetime"),
                    "ip_hash": _c("string", max_length=128),
                    "metadata": _c("json"),
                },
                tenant,
            ),
            filters=["player_id", "server_id"],
            sort=["id", "started_at", "ended_at"],
            allowed_actions=["list", "read"],
        ),
    ]


def expand_feature_packs(project: ProjectConfig) -> ProjectConfig:
    generated = [*messaging_resources(project), *social_resources(project), *gaming_resources(project)]
    existing = {(r.database, r.table) for r in project.resources}
    for resource in generated:
        if (resource.database, resource.table) not in existing:
            project.resources.append(resource)
    return project
