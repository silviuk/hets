# Changelog

## 1.1.0
- Implemented **Alternative B**:
  - Authenticates via EVCC's native session API (`POST /api/auth/login`) to acquire the `auth` JWT session cookie.
  - Corrected target API endpoint to the exact subrouter path: `POST /api/config/sponsortoken` with payload `{"token": "..."}`.
  - Added **Verification Check**: Queries `GET /api/state` after push to verify that EVCC accepted and actively instated the sponsor token.
  - Implemented automatic fallback to Alternative A (direct `evcc.db` SQLite update + `evcc.yaml` update + Supervisor restart) if the API or cloud check fails.

## 1.0.9
- Guaranteed token change execution only when the upstream token is genuinely different from the active token in memory and on disk (`evcc.yaml`).
- Auto-initializes state from existing `evcc.yaml` on container boot to prevent redundant updates and restarts.

## 1.0.8
- Added **Dual-Mode Update Engine**: If the EVCC REST API is locked or restricted, HETS automatically locates `evcc.yaml` on disk (`/addon_configs/` or `/config/`), updates `sponsortoken:`, and triggers an automated EVCC add-on restart via Home Assistant Supervisor.
- Added optional `evcc_password` setting to authenticate when EVCC has admin password protection enabled.

## 1.0.7
- Added **Smart Expiry Timing**: Sleeps automatically until 4 minutes prior to token expiration, then actively checks every 60s for the newly published token.

## 1.0.6
- Added automatic host LAN network interface detection (connects to EVCC on physical host IP e.g. `192.168.x.x:7070` when EVCC binds to external network interface).

## 1.0.5
- Added Home Assistant Supervisor API discovery for automatic EVCC add-on slug resolution.

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
