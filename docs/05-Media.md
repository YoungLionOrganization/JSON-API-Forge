# Media

The implemented v0.4 media backend is local filesystem storage. Media metadata is stored in Forge's internal database, while physical storage keys are not returned by the public API.

Controls include maximum file size, total/owner quota, MIME/extension rules, owner-aware delete policy, deduplication scope, signed temporary URLs and post-upload hooks. Owner quota accounting uses a transactionally maintained usage ledger to avoid concurrent over-allocation.

Batch upload reports per-item results instead of pretending partial success is atomic. If metadata persistence fails after streaming a new file, cleanup attempts remove the orphaned physical file.

Do not configure an unimplemented object-storage backend. Horizontal deployments need a shared/external storage architecture before they can safely treat media as multi-host.
