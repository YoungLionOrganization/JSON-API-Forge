# JSON Language Reference

Forge JSON is intentionally declarative. Values describe resources, operations and policies; they do not form a general-purpose expression language.

Use numbered fragments to structure a project. Later fragments merge into earlier configuration before strict typed validation. Lists and mappings follow the merge rules documented in `docs/36-Configuration-Merge-Semantics.md`; do not assume every nested list is magically merged item-by-item.

Use environment interpolation for secrets/URLs, JSON Schema for request payload shape, explicit permission strings for authorization, and `$body.*`, `$path.*`, `$query.*`, `$header.*`, `$param.*`, `$principal.*` sources for operation parameters.

Configured `pattern` and `patternProperties` expressions use Pydantic Core's linear-time regex engine and are limited to 512 characters. Look-around and backreferences are intentionally unsupported. String request parameters with a pattern default to a 16,384-character maximum unless a smaller `max_length` is declared.

When configuration becomes harder to understand than a small Python function, use a hook instead of adding clever indirection.
