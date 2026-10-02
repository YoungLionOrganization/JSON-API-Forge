# GitHub Release Assets

GitHub automatically provides **Source code (zip)** and **Source code (tar.gz)** for each official tag. Beginning with v0.4.2, CI also builds checked wheel/sdist artifacts. Branch-specific editor and library workflows produce downloadable build bundles but never publish a release automatically.

For each release: create the version tag, allow the tag CI/release gate to pass, create the GitHub Release from that exact tag, and use `RELEASE.md` as the release description.

Extra assets should be added only when they add something GitHub's source archive does not: a compiled CLI, signed installer, intentionally supported wheel/sdist, generated SDK bundle, offline docs bundle or an explicit container artifact/reference. Owner-published custom artifacts should have SHA-256 and preferably provenance/signatures.

Only the Project Owner or an authorized successor may publish Official Release assets under the project license.
