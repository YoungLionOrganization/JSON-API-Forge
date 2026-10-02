# Realtime Delivery and Backpressure

SSE/WebSocket channels are best-effort fanout. Memory mode is local to one process. Redis mode enables cross-instance pub/sub but still does not provide durable replay, ordering guarantees across arbitrary failures, or consumer acknowledgements like a real broker.

Each subscription/connection has bounded queue/connection settings. Slow clients must not create unbounded process memory. A channel may choose a smaller `max_message_bytes`, but configuration cannot exceed the process-wide 1 MiB WebSocket protocol ceiling. Message size is checked before JSON parsing where Forge parses signaling, and message-rate limits apply after connection establishment in addition to handshake/request protection.

Use a durable broker/outbox when losing a notification is unacceptable.
