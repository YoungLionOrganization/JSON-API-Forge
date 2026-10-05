# Editor v0.5.2 release assets

Download the `JSON-API-Forge-Editor-v0.5.2-release-assets` workflow artifact
after all six matrix jobs, the sanitizer job and the release-assets job pass.

For each architecture, attach both the multi-file portable ZIP and its native
installer to the separate `editor-v0.5.2` GitHub Release:

- Linux x64/ARM64: ZIP and Qt Installer Framework setup RUN;
- Windows x64/ARM64: ZIP and version-aware Inno Setup EXE;
- macOS Intel/ARM64: ZIP and Qt Installer Framework setup DMG.

The release-ready artifact keeps all 12 platform binaries at its top level:
six portable ZIPs, six native installers and their 12 `.sha256` sidecars. It
also contains `SHA256SUMS`, the license and release notes, plus the verified
all-platform bundle and its checksum. GitHub's automatic source archives are
not substitutes for these deployed Qt application trees. Do not publish a
lone executable as the Editor: Qt libraries, plugins, resources and WebEngine
helpers are required.

Linux/macOS installers use checksum-verified Qt Installer Framework 4.8.1.
Windows installers use checksum-verified Inno Setup 6.7.1. Every installer
presents the repository `LICENSE` as an agreement.
The macOS ARM64 application is native, while Qt's available IFW wrapper is x64
and runs through macOS Rosetta compatibility.

The workflow produces artifacts only. Release creation, signing, notarization
and final publication remain explicit Project Owner actions.

## Publication gate

Publish from an exact `Editor` commit only after both Editor workflows pass.
The bundle includes `release-build.json`, binding all 12 binaries to the
commit, workflow run, version and call-client revision. The bundle job runs
`editor/packaging/validate-release.py` before upload; missing assets, wrong
checksums, mismatched provenance and unexpected binaries stop the build.
Keep the server's `v0.5.2` release separate: its manual publisher accepts
server assets only. Use `editor-v0.5.2` for the Editor source tag and release.

## Windows Upgrade / Repair

The setup discovers existing installations through Windows registration and
checks the executable's numeric version. An older installation opens Upgrade;
the same version opens Repair. A newer version is preserved and a downgrade is
refused, including silent installations. Repair restores missing application
files; user projects and settings are kept. Legacy Qt IFW registrations at the
same migrated path are retired after successful installation. The Windows
build runs the real install/repair/legacy-upgrade/downgrade contract before
uploading an installer. Unrelated installed products are left alone.

Inno Setup is by Jordan Russell and Martijn Laan (https://jrsoftware.org/).
Linux/macOS retain the Qt IFW installation format.

Before publication, run `python editor/packaging/check-publication.py --sha SHA`
with GitHub CLI signed in. This read-only check requires the latest successful
build and CodeQL attempts, available assets and matching publication proof.
It refuses older successes after a failed rerun. It creates no release or tag.
