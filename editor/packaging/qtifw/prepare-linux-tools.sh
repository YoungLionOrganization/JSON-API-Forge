#!/usr/bin/env bash
set -euo pipefail

# Print only the tool directory to stdout (the workflow appends it to GITHUB_PATH).
binarycreator=${1:?usage: prepare-linux-tools.sh /path/to/binarycreator}
binarycreator=$(realpath "$binarycreator")
bin_dir=$(dirname "$binarycreator")
native_binarycreator=$binarycreator
library_path=${LD_LIBRARY_PATH:-}
if [[ $(uname -m) == aarch64 ]]; then
  # IFW 4.8.1 ARM64 tools require TIFF 5 AND WebP 6. Noble provides newer ABIs.
  # Keep verified Ubuntu compatibility libraries private to the build tool.
  # Never symlink incompatible ABIs or add this directory to the Editor runtime.
  compat_root="$bin_dir/../compat-libs"
  mkdir -p "$compat_root"
  package="$compat_root/libtiff5.deb"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 3 \
    --output "$package" \
    'https://ports.ubuntu.com/ubuntu-ports/pool/main/t/tiff/libtiff5_4.3.0-6ubuntu0.13_arm64.deb'
  echo "41db53b54493c6be85f357adfae747ff5d30c443a633e2199dc6ffa4143a88b4  $package" | sha256sum -c - >&2
  dpkg-deb -x "$package" "$compat_root"
  test -f "$compat_root/usr/lib/aarch64-linux-gnu/libtiff.so.5"
  package="$compat_root/libwebp6.deb"
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --retry 3 \
    --output "$package" \
    'https://ports.ubuntu.com/ubuntu-ports/pool/main/libw/libwebp/libwebp6_0.6.1-2ubuntu0.20.04.3_arm64.deb'
  echo "053fb0fae737303a7b1a502dfa07fe48a407656ccd7cfccb388116b7fa911d24  $package" | sha256sum -c - >&2
  dpkg-deb -x "$package" "$compat_root"
  test -f "$compat_root/usr/lib/aarch64-linux-gnu/libwebp.so.6"
  library_path="$compat_root/usr/lib/aarch64-linux-gnu${library_path:+:$library_path}"
  wrapper_dir="$bin_dir/../wrapped-bin"
  mkdir -p "$wrapper_dir"
  cat > "$wrapper_dir/binarycreator" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
ifw_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export LD_LIBRARY_PATH="$ifw_root/compat-libs/usr/lib/aarch64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ifw_root/bin/binarycreator" "$@"
WRAPPER
  chmod 0755 "$wrapper_dir/binarycreator"
  binarycreator="$wrapper_dir/binarycreator"
  bin_dir="$wrapper_dir"
fi
# Print the complete dependency closure instead of discovering one missing ABI
# per workflow run. Validate the installer template as well as the build tool.
for tool in "$native_binarycreator" "$(dirname "$native_binarycreator")/installerbase"; do
  printf 'Checking Qt IFW runtime dependencies: %s\n' "$tool" >&2
  dependencies=$(LD_LIBRARY_PATH="$library_path" ldd "$tool")
  printf '%s\n' "$dependencies" >&2
  if [[ $dependencies == *'not found'* ]]; then
    echo 'Qt IFW has unresolved runtime dependencies; aborting before the Editor build.' >&2
    exit 1
  fi
done
# Start the real tool immediately, before compiling and archiving the Editor.
"$binarycreator" --help >&2
printf '%s\n' "$bin_dir"
