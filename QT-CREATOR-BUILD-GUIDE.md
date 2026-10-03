# Build JSON API Forge Editor with Qt Creator

This guide builds the native Editor from the repository root and uses the checked-in `CMakePresets.json`. The presets are shared by Qt Creator and the command line, so both paths produce the same source configuration.

## 1. Install a matching toolchain

Install Qt 6.8.3 (Qt 6.4 or newer is supported) with these components:

- Qt Core, Gui, Widgets, Network and Test, using public Qt APIs only; the bounded ZIP decoder is included in the source;
- Qt WebEngineWidgets when you want embedded call rooms;
- Qt Creator, CMake and Ninja; Python 3.10+ for the optional packaging notice helper;
- Qt Installer Framework under **Developer and Designer Tools** when you want to create a setup package.

Platform compiler requirements:

- **Windows x64:** Visual Studio 2022 Build Tools with **Desktop development with C++**, plus the `MSVC 2022 64-bit` Qt package.
- **Windows ARM64:** ARM64 C++ tools and the `MSVC 2022 ARM64` Qt package. The current GitHub `windows-11-arm` image uses VS 2026; CI selects `Visual Studio 18 2026` (CMake 4.2+). Local VS 2022 installations can use their matching generator. Do not mix x64 Qt libraries with an ARM64 kit.
- **macOS:** Xcode command-line tools and the Qt package matching the Mac architecture.
- **Linux:** GCC or Clang, Ninja, and the Qt 6 development packages. On Ubuntu, the CI-equivalent base is `qt6-base-dev qt6-base-dev-tools libxcb-cursor0 ninja-build`; `qt6-webengine-dev` is optional.

## 2. Register the Qt kit

1. Open **Edit → Preferences → Kits** (on macOS, **Qt Creator → Settings → Kits**).
2. Under **Qt Versions**, add the `qmake` or Qt installation discovered by the Qt Maintenance Tool if it is not already listed.
3. Under **Kits**, select a compiler whose architecture exactly matches that Qt installation.
4. Select the bundled or system CMake and Ninja tools. A healthy kit shows no red warning icon.

On Windows, start Qt Creator from the Qt installation or a Visual Studio developer environment if the MSVC compiler is not detected automatically.

## 3. Open and configure the project

1. Choose **File → Open File or Project**.
2. Open the repository-root `CMakeLists.txt`—not `editor/CMakeLists.txt` directly.
3. In **Configure Project**, select the matching kit.
4. Choose the checked-in **Developer build** (`dev`) preset for debugging or **Release build** (`release`) for a distributable binary.
5. Confirm that `BUILD_TESTING=ON`. Keep `FORGE_EDITOR_WARNINGS_AS_ERRORS=ON` so local builds enforce the same warning policy as CI.

Qt Creator should populate `build/dev` or `build/release`. If it instead proposes a shadow-build path, open **Projects → Build Settings → CMake** and select the corresponding preset.

## 4. Build, test and run

Select the `JSONAPIForgeEditor` target, then use **Build → Build Project**. The runnable executable is named `JSON-API-Forge-Editor` (`.exe` on Windows).

Run the tests from **Tools → Tests → Test Results → Run All Tests**, or use Qt Creator's terminal:

```bash
ctest --preset dev
```

Useful visual-regression launch arguments are:

```text
--window-size 1024x640 --screenshot editor-preview.png
--team-preview --window-size 1180x720 --screenshot team-preview.png
--graph-preview --window-size 1180x720 --screenshot graph-preview.png
```

## 5. Stage a portable build

From the repository root in Qt Creator's terminal:

```bash
cmake --build --preset release
ctest --preset release
cmake --install build/release --prefix build/stage
```

On Windows, deploy the Qt runtime next to the staged executable before creating a ZIP:

```powershell
$exe = Get-ChildItem build/stage -Filter JSON-API-Forge-Editor.exe -Recurse | Select-Object -First 1
windeployqt --release --no-translations --compiler-runtime $exe.FullName
Copy-Item EDITOR.md,LICENSE -Destination build/stage
python editor/packaging/stage-notices.py build/stage
Compress-Archive -Path build/stage/* -DestinationPath JSON-API-Forge-Editor-v0.5.1-windows-x64.zip
```

For ARM64, run these commands from the ARM64 kit and use an architecture-appropriate output name.

On Linux, make the selected Qt kit's `qtpaths6` (or `qtpaths`) available on
`PATH`, then bundle its runtime and install the launcher:

```bash
bash editor/packaging/linux/bundle-qt.sh build/stage
install -m 0755 editor/packaging/linux/launcher.sh build/stage/json-api-forge-editor
cp EDITOR.md LICENSE build/stage/
python editor/packaging/stage-notices.py build/stage
(cd build/stage && zip -9 -r ../../JSON-API-Forge-Editor-v0.5.1-linux-x64.zip .)
```

On macOS, deploy the exact bundle produced by the selected kit:

```bash
macdeployqt build/stage/JSON-API-Forge-Editor.app -always-overwrite
cp EDITOR.md LICENSE build/stage/
python editor/packaging/stage-notices.py build/stage
(cd build/stage && zip -9 -r ../../JSON-API-Forge-Editor-v0.5.1-macos-arm64.zip .)
```

Use `macos-x64` in the archive name when using an Intel kit. The executable
inside the bundle is `Contents/MacOS/JSON-API-Forge-Editor`.

## 6. Build a desktop installer with Qt Installer Framework

Ensure `binarycreator.exe` from Qt Installer Framework is on `PATH`, then run:

```powershell
./editor/packaging/qtifw/build-installer.ps1 `
  -StageDir build/stage `
  -OutputFile JSON-API-Forge-Editor-v0.5.1-windows-x64-setup.exe
```

On Linux or macOS, ensure `binarycreator` is on `PATH`, deploy the Qt runtime as described by the platform workflow, and run:

```bash
# Linux
bash editor/packaging/qtifw/build-installer.sh \
  build/stage JSON-API-Forge-Editor-v0.5.1-linux-x64-setup.run linux

# macOS
bash editor/packaging/qtifw/build-installer.sh \
  build/stage JSON-API-Forge-Editor-v0.5.1-macos-arm64-setup.dmg macos
```

The helpers consume the same Qt IFW metadata as GitHub Actions. They embed the staged application, platform icons, maintenance/uninstall tool and the repository `LICENSE`; Windows additionally receives Start Menu/Desktop shortcuts. The license is shown as an agreement during setup and is also installed with the product.

The shared Amber Gold / Graphite stylesheet is
`editor/packaging/qtifw/config/installer.qss`. Both helpers embed it automatically;
keep it beside the XML configurations. Use the application/window icons for
branding: do not add a full-size logo as a wizard header pixmap.

Linux defaults to `~/Applications/JSON API Forge Editor` and does not require
administrator privileges. Choose a writable destination. On Ubuntu 24.04 ARM64,
install the workflow's native dependencies, then prepare IFW's private TIFF 5
and WebP 6 compatibility libraries before invoking the builder:

```bash
ifw_tool_dir=$(bash editor/packaging/qtifw/prepare-linux-tools.sh /path/to/qt-ifw/bin/binarycreator)
export PATH="$ifw_tool_dir:$PATH"
```

The helper checks both tool and installer-template dependencies. Do not copy its
compatibility libraries into the Editor's runtime or replace system libraries.
On macOS, the builder creates a setup `.app`, then a verified compressed DMG;
failed DMG creation is retried with the original diagnostic output preserved.

The pinned Qt IFW 4.8.1 macOS installer tool is Intel-only and requires Rosetta
on Apple Silicon. The packaged Editor itself uses its native ARM64 kit.

The CMake install step also installs legal notices and Qt license copies. Preserve these files when preparing portable ZIPs and installer payloads. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency notices and corresponding-source obligations.

## Troubleshooting

- **Qt6Config.cmake not found:** the kit does not point at the selected Qt installation. Fix the Qt version in the kit instead of hard-coding a machine-specific `CMAKE_PREFIX_PATH` into the repository.
- **Wrong machine type or linker errors:** the compiler architecture and Qt package architecture do not match. Delete the affected `build/dev` or `build/release` directory after correcting the kit, then configure again.
- **Old private ZIP header errors:** remove the old build directory and configure this complete source tree again. `SafeZipReader` and the bundled `puff` decoder require no Qt private headers.
- **Qt IFW cannot load `libxcb-cursor.so.0`:** install `libxcb-cursor0` on Ubuntu before running the official IFW installer or `binarycreator`.
- **WebEngine is missing:** install Qt WebEngine for the exact Qt version/architecture. The Editor still builds without it and uses the secure external-browser fallback.
- **`binarycreator` not found:** install Qt Installer Framework from the Qt Maintenance Tool and add its `bin` directory to the Qt Creator terminal environment.
- **Stale UI or resource output:** use **Build → Clean Project**, delete only the selected preset's build directory if needed, and reconfigure. Do not reuse one build directory across x64 and ARM64 kits.
