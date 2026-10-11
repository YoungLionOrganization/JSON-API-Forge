# Publish v0.5.2 in one workflow run

The four v0.5.2 release drafts already contain their verified packages. The combined workflow publishes all four under their separate tags: `v0.5.2`, `editor-v0.5.2`, `python-library-v0.5.2` and `exampleApps-v0.5.2`.

1. Open [Actions → Validate or publish all components](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/publish-release.yml).
2. Click **Run workflow** and choose branch **main**.
3. Keep **component = all** and leave **source_sha** empty.
4. Choose **mode = validate** to check every source, workflow, tag, proof and remote asset. This does not publish anything.
5. After validation succeeds, run once more with **mode = publish**. That run validates again and publishes all four components.

SHA copying is no longer needed. The reviewed [publication plan](release/components-v0.5.2.json) supplies the exact source commits and asset hashes. If you use the optional SHA consistency check, surrounding spaces/newlines are removed; a genuinely different SHA is still refused. In `all` mode that optional field checks only Main.

The workflow requires the authorized owner account defined in `release/authorized_publishers.json`. Its controller commit must also have successful Main CI and CodeQL. Use a new **Run workflow** after updating the publisher, so the new workflow definition and tests are used.

## Recovery

All selected components pass preflight before any tag creation or public transition. GitHub does not offer an atomic transaction covering four releases, so a network failure can occur after some releases become public. Run the same workflow again: it verifies those public releases without changing them and completes the remaining drafts.

The `component-publication-validation` and `component-publication-result` workflow artifacts contain JSON reports with component status and release links, including when a run fails. An unexpected asset, digest, tag or source stops publication; the publisher never overwrites mismatching public releases.

Missing or incomplete drafts must be restored from the verified build artifacts before publication. The workflow deliberately reuses reviewed draft packages rather than rebuilding or silently uploading replacements. If a source/build workflow is rerun, reverify its packages and update the pinned plan before publishing.

## Release scope

Selecting one component publishes only that component. `all` publishes all four in Main → Editor → Python SDK → examples order. Existing prerelease settings are preserved; Editor, SDK and examples are currently Alpha prereleases. Editor binaries do not include publisher code signing or macOS notarization.

GitHub publication does not upload packages to PyPI. The SDK's unchanged wheel and source distribution require a separate PyPI upload if that distribution channel is desired.
