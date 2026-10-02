# Media Consistency, Quotas and Batch Semantics

Media upload must coordinate streamed bytes, filesystem state, metadata rows and quota accounting. Owner usage is reserved transactionally so two concurrent uploads cannot both exceed an owner quota. Metadata is the authority for API visibility; physical storage keys remain internal.

Deduplication defaults to owner scope to avoid leaking cross-owner existence. Batch upload intentionally returns partial item status instead of claiming all-or-nothing behavior across streamed files.

If metadata commit fails after a new object is written, cleanup should remove the object. Operators should still monitor orphaned files and back up metadata/files together when consistency matters.
