# Changelog

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
