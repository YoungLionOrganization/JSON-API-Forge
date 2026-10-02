# Known Limitations

JSON API Forge v0.4.x is an **Alpha hardening line**, not an unconditional production certification.

Important limits include: feature packs are primitives rather than complete products; realtime is best-effort; SQL idempotency cannot make external systems exactly-once; media is local-filesystem only; file data sources are not distributed databases; egress validation does not replace network controls; declarative SQL is trusted server configuration; memory backends are process-local; short API-key auth caching creates a bounded cross-worker revocation window; Passenger is not ideal for sustained realtime traffic; schema auto-creation is not a full migration platform; audit storage is not an immutable compliance ledger.

See `docs/41-Known-Limits-and-Non-Goals.md` for engineering detail and mitigations.
