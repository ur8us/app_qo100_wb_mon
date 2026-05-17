# Agent Notes

This is a MaixPy application for MaixCAM/MaixCAM2. Keep the runtime dependency-free: the target camera has Python socket, ssl, select, struct, requests, and MaixPy modules, but no websocket-client package.

Use `scripts/package.sh` to build `dist/qo100_wb_mon.zip` and `scripts/deploy.sh root@<camera-host>` to install it with `app_store_cli`.

The app intentionally connects directly to BATC's QO-100 wideband FFT WebSocket feed and renders the spectrum with MaixPy drawing primitives. Preserve the DNS fallback path to `185.83.169.27` because camera DNS can be misconfigured in the field.

Do not commit device passwords, local Tailscale hostnames, generated logs, or temporary screenshots outside `docs/images/`.
