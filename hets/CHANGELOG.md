# Changelog

## 1.0.3
- Switched HTTP engine to pure Python standard library (`urllib.request`) for zero external dependencies (resolves `ModuleNotFoundError: No module named 'requests'`).

## 1.0.2
- Fixed container init startup conflict by setting `init: false` (resolves `s6-overlay-suexec: fatal: can only run as pid 1`).

## 1.0.1
- Standardized to latest Home Assistant App schema (`apptype: app`, `stage: stable`).

## 1.0.0
- Initial release of HETS Home Assistant App.
