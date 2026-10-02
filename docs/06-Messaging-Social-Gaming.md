# Messaging, Social and Gaming Packs

Feature packs create backend primitives such as messages, posts, profiles, saves, inventory, achievements and leaderboard records. They are not complete products or anti-cheat systems.

Identity-sensitive fields are server controlled where appropriate. For example author/sender ownership should come from the authenticated principal, not a client-writable field. High-authority game state is intentionally read-only through generic CRUD where mutation would normally require validated server business logic.

Use feature packs to start a schema and permission model, then add domain-specific RPC/hooks for moderation, economy, anti-cheat, progression rules and external integrations.
