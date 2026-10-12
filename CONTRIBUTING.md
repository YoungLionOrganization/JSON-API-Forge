# Contributing to JSON API Forge Editor

Read `LICENSE`, `LICENSE-FAQ.md`, `GOVERNANCE.md` and
`CONTRIBUTOR_LICENSE_AGREEMENT.md` before contributing. Report vulnerabilities
through `SECURITY.md`; do not disclose credentials or exploit details in a
public issue.

The Editor branch must remain Editor-only. Do not add the Python server, SDK,
example projects, deployment templates or their workflows here.

## Required local checks

```bash
cmake -S . -B build/editor -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DFORGE_EDITOR_WARNINGS_AS_ERRORS=ON
cmake --build build/editor --parallel
ctest --test-dir build/editor --output-on-failure
python scripts/check_manifest.py
git diff --check
```

Test success and failure paths for security-sensitive changes. Never persist
passwords, founder setup secrets, bearer sessions or call tickets. Native
plugin changes must preserve path containment, no-symlink, digest, API-version,
declared-permission and explicit-enable checks.

For cross-component graph checks, set `FORGE_EDITOR_EXAMPLES_APP_ROOT` to the
`app` directory of a separately checked-out exampleApps branch, then run
`forge_editor_tests graphExamplesCompilerContract` from the native build.
The check compiles every domain-transition graph and compares the entire
resulting operation with its target RPC definition. It skips when the variable
is absent; the Editor source distribution does not include the example apps.

Pull requests should explain behavior, compatibility, security impact, tests
and packaging impact. Contribution forks are permitted only within the narrow
collaboration exception described by the project license.

## Provenance and AI assistance

Follow [AI_USAGE_POLICY.md](AI_USAGE_POLICY.md). Identify material AI assistance and third-party code/assets, preserve upstream notices, and record human review and tests. Read [TRADEMARK_POLICY.md](TRADEMARK_POLICY.md) before using the project identity in an integration or contribution fork.
