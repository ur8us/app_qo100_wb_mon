#!/usr/bin/env sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
dist_dir="$root_dir/dist"
package="$dist_dir/qo100_wb_mon.zip"

mkdir -p "$dist_dir"
rm -f "$package"

cd "$root_dir"
zip -qr "$package" app.yaml main.py README.md assets

printf '%s\n' "$package"
