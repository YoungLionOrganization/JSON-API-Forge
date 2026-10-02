# Configuration Merge Semantics

Fragments are loaded in deterministic alphabetical order. Mappings merge recursively where the loader defines mapping merge behavior; later scalar values replace earlier values. Lists should be treated as complete list values unless the documented loader/model specifically provides keyed expansion behavior.

This deterministic model is why numeric prefixes are recommended. Avoid two fragments that both “half-own” the same security object unless the merge result is obvious. Run `forge validate` after refactors and inspect the resulting routes/OpenAPI rather than assuming a merge did what you intended.

Unknown fields are rejected after merge. Editor schemas help at file-edit time, but only the runtime loader sees the complete merged configuration.
