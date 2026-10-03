# Editor v0.5.2 release assets

Download the `JSON-API-Forge-Editor-v0.5.2-release-assets` workflow artifact
after all six matrix jobs, the sanitizer job and the release-assets job pass.

For each architecture, attach both the multi-file portable ZIP and its native
installer to the v0.5.2 GitHub Release:

- Linux x64/ARM64: ZIP and Qt Installer Framework setup RUN;
- Windows x64/ARM64: ZIP and Qt Installer Framework setup EXE;
- macOS Intel/ARM64: ZIP and Qt Installer Framework setup DMG.

The release-ready artifact keeps all 12 platform binaries at its top level:
six portable ZIPs, six native installers and their 12 `.sha256` sidecars. It
also contains `SHA256SUMS`, the license and release notes, plus the verified
all-platform bundle and its checksum. GitHub's automatic source archives are
not substitutes for these deployed Qt application trees. Do not publish a
lone executable as the Editor: Qt libraries, plugins, resources and WebEngine
helpers are required.

Every installer is created by a checksum-verified official Qt Installer
Framework 4.8.1 tool and presents the repository `LICENSE` as an agreement.
The macOS ARM64 application is native, while Qt's available IFW wrapper is x64
and runs through macOS Rosetta compatibility.

The workflow produces artifacts only. Release creation, signing, notarization
and final publication remain explicit Project Owner actions.
