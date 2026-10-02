# Known Limits and Non-Goals

Forge is not trying to be: a visual no-code business-logic builder, an arbitrary attacker-safe SQL execution service, a durable event broker, an object-storage system in v0.4, a complete database migration platform, an immutable compliance ledger, an identity provider, or a replacement for network/database security.

It is a backend framework that makes common infrastructure declarative while keeping real domain logic and trust boundaries reviewable.

Alpha status means applications must still perform their own threat modeling, load testing, data migration validation, observability setup, backups and incident planning. The canonical CI verifies framework behavior across supported primitives; it cannot certify every configuration/deployment.
