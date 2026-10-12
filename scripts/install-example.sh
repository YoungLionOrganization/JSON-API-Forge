#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <ExampleName> [destination-app-directory]" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/.." && pwd)
name=$1
if [[ ! "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$ ]]; then
  echo "Example name contains unsafe characters: $name" >&2
  exit 2
fi
destination_root=${2:-"$repository_root/app"}
# Git Bash accepts drive paths; dirname otherwise reaches a fixed point at C:.
if [[ "$destination_root" =~ ^[A-Za-z]:[/\\] ]] && command -v cygpath >/dev/null 2>&1; then
  destination_root=$(cygpath -u -- "$destination_root")
fi
while [[ "$destination_root" != / && "$destination_root" == */ ]]; do
  destination_root=${destination_root%/}
done
source_dir="$repository_root/app/$name"
target_dir="$destination_root/$name"

if [[ -L "$repository_root/app" || -L "$source_dir" || ! -f "$source_dir/app.json" ]]; then
  echo "Unknown example: $name" >&2
  exit 2
fi
if find "$source_dir" -type l -print -quit | grep -q .; then
  echo "Refusing to copy an example that contains symbolic links: $name" >&2
  exit 1
fi
if [[ -e "$target_dir" || -L "$target_dir" ]]; then
  echo "Refusing to overwrite existing target: $target_dir" >&2
  exit 1
fi

ancestor=$destination_root
while [[ "$ancestor" != / && "$ancestor" != . ]]; do
  if [[ -L "$ancestor" ]]; then
    echo "Refusing a symbolic-link destination parent: $ancestor" >&2
    exit 1
  fi
  parent=$(dirname -- "$ancestor")
  if [[ "$parent" == "$ancestor" ]]; then
    break
  fi
  ancestor=$parent
done
mkdir -p -- "$destination_root"
staging=$(mktemp -d "$destination_root/.forge-example.XXXXXXXX")
trap 'rm -rf -- "$staging"' EXIT
cp -R -- "$source_dir" "$staging/$name"
if [[ -e "$target_dir" || -L "$target_dir" ]]; then
  echo "Target appeared during staging: $target_dir" >&2
  exit 1
fi
# Move into the known parent. A late target must never receive a nested copy.
mv -n -- "$staging/$name" "$destination_root/"
if [[ -e "$staging/$name" ]]; then
  echo "Target appeared during installation: $target_dir" >&2
  exit 1
fi
echo "Installed $name at $target_dir"
echo "Next: run 'forge init', 'forge validate', and 'forge dev' in the destination checkout."
