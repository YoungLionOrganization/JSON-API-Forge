# Discord Economy and PostgreSQL

The bundled App1 demonstrates a Discord-style economy without giving the bot SQL credentials. A narrow API key calls predefined operations such as `economy.balance`, `economy.grant` and `economy.transfer`.

Transfers use one database transaction, row-count guards and idempotency. Reuse a Discord interaction/message identifier as the `Idempotency-Key`; retrying the same command then replays the completed response instead of charging twice. v0.4.1 also guarantees an HTTP replay marker and does not re-run background hooks on replay.

This exactly-once-like protection is limited to side effects in the selected SQL transaction. If a transfer also calls Discord, sends email or writes another database, use an outbox/inbox/provider idempotency pattern.
