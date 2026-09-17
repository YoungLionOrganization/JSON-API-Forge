# Editor v0.5.1 — workflow and installer theme repair

This package contains only the Editor source tree. The product version remains
0.5.1. Baseline: `1ffb40d39a09cc6158bd72420ef6ce84416a31e4` on `Editor`.
Last reviewed: 13 September 2026.

## Workflow changes

The latest [Editor build run](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/runs/34478415153)
has three failing packaging jobs. Its native Editor tests passed on the platforms
that reached compilation; CodeQL and the sanitizer job passed as well.

- **Linux x64:** the generated installer's CLI test requested administrator
  privileges even though the destination was a writable temporary directory.
  Linux now defaults to `~/Applications/JSON API Forge Editor`, and its staged
  package metadata does not require administrator rights. The Windows/macOS
  privilege policy is preserved. The workflow still installs the generated setup
  and launches the installed Editor; the failing check has not been removed.
- **Linux ARM64:** IFW 4.8.1's `binarycreator` needs both `libtiff.so.5` and
  `libwebp.so.6`. The previous repair supplied only TIFF. Both Ubuntu compatibility
  packages are now SHA256 pinned and kept in a directory used only by the build
  tool. Noble supplies the other dependencies, including WebP mux/demux. Setup
  prints and validates the complete dependency closure of `binarycreator` and
  `installerbase` before compilation, then starts the tool with `--help`.
  These compatibility libraries are not added to the Editor or its installer.
- **macOS ARM64:** the packaging step failed without a diagnostic identifying
  the internal command. [Qt IFW 4.8.1's implementation](https://github.com/qtproject/installer-framework/blob/4.8.1/src/libs/ifwtools/binarycreator.cpp)
  does not check the exit status or print stderr from its internal `hdiutil`
  invocation, then deletes the intermediate app bundle. The new builder creates
  that `.app` explicitly, creates the compressed DMG separately with up to three
  attempts, and verifies the image. The workflow mounts the result, checks for
  the launchable setup app and runs its help command. Errors and build logs are
  retained. The original silent failure's exact `hdiutil` error cannot be recovered
  from the old log; native macOS confirmation is still required after upload.

## Installer appearance

All three platforms share `editor/packaging/qtifw/config/installer.qss`:
Graphite surfaces, Amber Gold titles and primary buttons, readable license and
destination fields, visible keyboard focus, disabled button states, and a
matching progress bar. Both builders include the stylesheet in the installer.

The standard Qt wizard, navigation and license consent remain in place. The
760x540 logical window size is preserved. Large logo/header/background pixmaps
remain absent, so they cannot cover the controls again. The Linux corner resize
grip no longer paints over the Cancel button.

## Verification performed for this repair

- Both workflows passed actionlint 1.7.12; shell syntax and Editor-only source
  preflight passed.
- Eight packaging regression tests passed, including Linux privilege metadata,
  staged theme, missing payload, DMG retries, creation failure, verification
  failure and mount cleanup.
- Official SHA256-verified IFW 4.8.1 Linux x64 created and installed a test
  payload using the production packaging scripts.
- The actual ARM64 `binarycreator` ran under QEMU against Ubuntu Noble libraries
  plus the two private compatibility packages. It created an ARM64 installer;
  that installer successfully installed the test payload under QEMU using only
  Noble libraries. The legacy TIFF/WebP libraries were not required by the setup.
- The real Linux installer GUI was inspected at 100% and 150% scaling. At 150%,
  keyboard navigation, destination editing, license consent and installation
  completed through the Finished page. No stylesheet/parser warnings occurred.
- The source manifest and the extracted deliverable ZIP were verified.

The packaging payload used locally was a small executable fixture, not a new
native Editor application build. This repair does not change C++ application
code. The sandbox could not switch to an unprivileged Linux UID, so the revised
non-administrator installation path still needs its native GitHub runner check.
Windows and macOS were not run locally. No remote workflow was triggered or
reported green for these unpushed changes.

## Upload only Editor

Extract the ZIP into a new folder. Open PowerShell in the extracted
`JSON-API-Forge-Editor-v0.5.1` directory and run:

```powershell
.\PUSH-EDITOR.bat
```

Type `YUKLE` after the source checks. Requires Git for Windows and Windows
PowerShell. The script uses your existing GitHub authentication and uploads only
`refs/heads/Editor`. It retains the current remote Editor commit as its parent
and uses a normal push. Concurrent remote changes cause a rejection instead of
being overwritten. Other branches, tags and releases are not modified.

For source verification without a GitHub operation:

```powershell
.\PUSH-EDITOR.bat --check
```

Use the included Editor uploader, not the old four-branch replacement script.
This ZIP contains source code; the workflow generates the installers and
portable application archives after upload.
