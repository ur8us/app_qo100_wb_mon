#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s root@<camera-host>\n' "$0" >&2
    exit 2
fi

target="$1"

ssh "$target" 'set -eu
mkdir -p /etc/systemd/resolved.conf.d
cat >/etc/systemd/resolved.conf.d/qo100-wb-mon.conf <<'"'"'EOF'"'"'
[Resolve]
DNS=1.1.1.1 8.8.8.8 9.9.9.9
FallbackDNS=1.0.0.1 8.8.4.4
Domains=~.
EOF
systemctl restart systemd-resolved
getent hosts eshail.batc.org.uk
python3 - <<'"'"'PY'"'"'
import requests
r = requests.get("https://eshail.batc.org.uk/wb/", timeout=8)
print("https", r.status_code, len(r.text))
PY'
