# Python SDK v0.5.2 release checklist

## Source and identity

- [ ] The branch is `python-library` and contains no server, Editor, examples or deployment tree.
- [ ] `pyproject.toml`, `VERSION` and `json_api_forge/_version.py` are exactly `0.5.2`.
- [ ] The distribution name is `json-api-forge-client`; the import remains `json_api_forge`.
- [ ] `python scripts/check_manifest.py` passes on the exact commit.

## Required gates

- [ ] Ruff format/lint and compilation pass.
- [ ] Unit tests pass for Python 3.10–3.14 across Linux, Windows and macOS targets.
- [ ] Windows ARM64 and Linux ARM64 jobs pass without native server dependencies.
- [ ] Debian, Arch, Fedora, Rocky Linux 9/cPanel-family, openSUSE and Alpine installs pass.
- [ ] The SDK-to-`main` control-plane contract passes.
- [ ] Aggregate and critical-module coverage gates pass.
- [ ] CodeQL is green.
- [ ] Two wheel builds match byte-for-byte and sdist payloads match.
- [ ] The wheel is `py3-none-any`, contains only SDK packages and installs outside the checkout.

- [ ] Release tag is `python-library-v0.5.2`; server and Editor tags are separate.

## Publish

- [ ] Download the Action artifact and verify `SHA256SUMS`.
- [ ] Run `python -m twine check` once more on the downloaded wheel/sdist.
- [ ] Upload the unchanged wheel/sdist to TestPyPI or PyPI manually.
- [ ] Create/attach GitHub Release assets only from the same reviewed commit.
- [ ] Do not publish while any required workflow is red.
