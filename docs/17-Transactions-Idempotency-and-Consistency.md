# Transactions, Idempotency and Consistency

For an idempotent SQL operation, Forge computes a key identity from project + operation + principal + raw key and a canonical fingerprint from every declarative request input that can affect the operation. The idempotency row, SQL side effects and stored response are committed in one database transaction.

A crash before commit rolls everything back. A crash after commit leaves a completed record that can replay. Concurrent duplicate inserts rely on the unique constraint; an identical completed request replays, a different fingerprint conflicts, and an in-progress collision returns conflict/retry guidance.

v0.4.1 distinguishes replay transport metadata from the persisted canonical response: object replays add `_idempotent_replay: true` and a response header without modifying stored first-execution data. Cache invalidation and background hooks run only for the first execution.

Idempotency cannot make external APIs, email, Discord, another database or a queue participate in the SQL transaction. Use an outbox/inbox or provider idempotency for cross-system effects.
