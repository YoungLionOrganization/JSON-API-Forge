#!/usr/bin/env bash
set -euo pipefail

source_app=${1:?usage: create-dmg.sh INSTALLER.app OUTPUT.dmg}
output=${2:?usage: create-dmg.sh INSTALLER.app OUTPUT.dmg}
test -d "$source_app/Contents/MacOS"
case "$output" in *.dmg) ;; *) echo 'Output must end in .dmg' >&2; exit 2 ;; esac

# Give hdiutil a containing folder so the mounted image has a launchable .app
# at its root, rather than depending on special handling of app bundles.
image_root=$(mktemp -d)
trap 'rm -rf -- "$image_root"' EXIT
cp -a "$source_app" "$image_root/"

# IFW 4.8.1 silently ignores hdiutil failures and deletes its .app afterwards.
# Build the .app first, and create/verify the image ourselves. Keep the source
# bundle intact across retries; never publish a partially created image.
for attempt in 1 2 3; do
  printf 'Creating installer DMG (attempt %s/3): %s\n' "$attempt" "$output"
  if hdiutil create -ov -format UDZO -fs HFS+ \
      -volname 'JSON API Forge Editor Setup' -srcfolder "$image_root" "$output"; then
    hdiutil verify "$output"
    exit 0
  fi
  printf 'hdiutil create failed on attempt %s/3\n' "$attempt" >&2
  if [[ $attempt != 3 ]]; then sleep 3; fi
done
echo 'Installer DMG creation failed after three attempts.' >&2
exit 1
