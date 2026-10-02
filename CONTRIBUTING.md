# Contributing to JSON API Forge

Thank you for improving the canonical JSON API Forge project. The project is source-available and centrally governed; contributions are accepted upstream while alternative public distributions are restricted by `LICENSE`.

## Before you start
1. Read `LICENSE`, `LICENSE-FAQ.md`, `GOVERNANCE.md` and `CONTRIBUTOR_LICENSE_AGREEMENT.md`.
2. Open an issue before substantial architecture changes.
3. Use `SECURITY.md` for vulnerabilities.

## Contribution forks
The license provides a narrow collaboration-platform fork exception solely for preparing pull requests. Do not publish releases, packages, images, mirrors or separately maintained editions from such a fork.

## Development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
forge init
forge validate
forge doctor
forge schema
git diff --exit-code -- schemas
python -m compileall -q framework app tests
pytest --cov=framework --cov-report=term-missing -q
```

Add tests for success, failure and concurrency behavior when relevant. Update documentation and generated schemas for declarative changes. Do not hide security-sensitive business logic inside overly generic configuration.

A pull request should explain motivation, behavior, compatibility, tests and documentation. Submission is governed by the project CLA when acknowledged in the PR workflow.

## Provenance and AI assistance

Follow [AI_USAGE_POLICY.md](AI_USAGE_POLICY.md). Identify material AI assistance and third-party code/assets, preserve upstream notices, and record human review and tests. Read [TRADEMARK_POLICY.md](TRADEMARK_POLICY.md) before using the project identity in an integration or contribution fork.
