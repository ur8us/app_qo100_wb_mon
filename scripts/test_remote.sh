#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    printf 'Usage: %s root@<camera-host> [screenshot-path]\n' "$0" >&2
    exit 2
fi

target="$1"
out="${2:-docs/images/maixcam-qo100-wb-mon.jpg}"
remote_screenshot="/tmp/qo100_wb_mon_screen.jpg"
remote_log="/tmp/qo100_wb_mon_test.log"

ssh "$target" 'set -eu
systemctl stop launcher.service 2>/dev/null || true
old=$(cat /tmp/qo100_wb_mon.pid 2>/dev/null || true)
if [ -n "$old" ]; then kill "$old" 2>/dev/null || true; fi
pkill -f "[p]ython3 -u main.py" 2>/dev/null || true
pkill -f "[p]ython3 main.py" 2>/dev/null || true
sleep 1
rm -f /tmp/qo100_wb_mon_screen.jpg /tmp/qo100_wb_mon_test.log
cd /maixapp/apps/qo100_wb_mon
setsid env PYTHONUNBUFFERED=1 QO100_WB_SCREENSHOT=/tmp/qo100_wb_mon_screen.jpg QO100_WB_SCREENSHOT_AFTER=10 python3 -u main.py >/tmp/qo100_wb_mon_test.log 2>&1 &
echo $! >/tmp/qo100_wb_mon.pid
'

sleep 13
scp "$target:$remote_screenshot" "$out"
ssh "$target" "tail -80 '$remote_log'"
printf 'Saved %s\n' "$out"
