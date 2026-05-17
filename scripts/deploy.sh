#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s root@<camera-host>\n' "$0" >&2
    exit 2
fi

target="$1"
root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
package=$("$root_dir/scripts/package.sh")
remote_package="/tmp/qo100_wb_mon.zip"

scp "$package" "$target:$remote_package"
ssh "$target" "app_store_cli install $remote_package"
