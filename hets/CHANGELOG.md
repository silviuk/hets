# Changelog

## 1.0.8
- Added **Dual-Mode Update Engine**: If the EVCC REST API is locked or restricted, HETS automatically locates `evcc.yaml` on disk (`/addon_configs/` or `/config/`), updates `sponsortoken:`, and triggers an automated EVCC add-on restart via Home Assistant Supervisor.
- Added optional `evcc_password` setting to authenticate when EVCC has admin password protection enabled.

## 1.0.7
- Added **Smart Expiry Timing**: Sleeps automatically until 4 minutes prior to token expiration, then actively checks every 60s for the newly published token.
- Verified and expanded EVCC configuration endpoints (`/api/sponsortoken`, `/config/sponsortoken`, `/sponsortoken`) supporting JSON and raw payloads.

## 1.0.6
- Added automatic host LAN network interface detection (connects to EVCC on physical host IP e.g. `192.168.x.x:7070` when EVCC binds to external network interface).
- Made `evcc_url` optional with automatic fallback probing.

## 1.0.5
- Added Home Assistant Supervisor API discovery for automatic EVCC add-on slug resolution.
- Added detailed diagnostic error logging per endpoint.

## 1.0.4
- Added `host_network: true` and automatic endpoint discovery across `127.0.0.1`, `localhost`, `homeassistant.local`, and container hostnames.

## 1.0.3
- Switched HTTP engine to pure Python standard library (`urllib.request`) for zero external dependencies.

## 1.0.2
- Fixed container init startup conflict by setting `init: false`.

## 1.0.1
- Standardized to latest Home Assistant App schema (`apptype: app`, `stage: stable`).

## 1.0.0
- Initial release of HETS Home Assistant App.
