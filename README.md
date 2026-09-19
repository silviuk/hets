# Home Assistant EVCC Test Token Auto-Sync

[![GitHub Release](https://img.shields.io/github/v/release/silviuk/ha-evcc-token-sync?color=blue)](https://github.com/silviuk/ha-evcc-token-sync/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

A Home Assistant Add-on and automation toolkit that automatically detects when a new or renewed **evcc** trial/test sponsor token is published, extracts it, and hot-reloads it directly into your EVCC instance via its REST API.

---

## Features

- ⚡ **Automated Extraction**: Scrapes official documentation and repositories for newly released test JWT sponsor tokens.
- 🔄 **Live Hot-Reload**: Pushes the token directly to the EVCC REST API (`POST /api/sponsortoken`) without requiring an EVCC restart.
- ⏱️ **Expiry Inspection**: Decodes the token's `exp` claim to ensure only valid, newer tokens are applied.
- 🔔 **Home Assistant Notifications**: Sends native persistent notifications whenever a token is renewed.
- 📦 **Two Deployment Modes**: Use as a standard **Home Assistant Add-on** (via repository or local) or as a **Standalone Python Script / Automation**.

---

## Installation Methods

### Method 1: Home Assistant Add-on Repository (Recommended)

1. In Home Assistant, navigate to **Settings > Add-ons > Add-on Store**.
2. Click the three dots (top right) -> **Repositories**.
3. Add repository URL:
   ```text
   https://github.com/silviuk/ha-evcc-token-sync
   ```
4. Find **EVCC Test Token Auto-Sync** in the list and click **Install**.
5. Adjust configuration if needed (under the *Configuration* tab) and start the add-on.

---

### Method 2: Manual Local Add-on

1. Copy the `evcc-token-sync/` directory into your Home Assistant `/addons/` directory (e.g. `/addons/evcc-token-sync/`).
2. Open **Settings > Add-ons > Add-on Store**.
3. Click the menu (top right) -> **Check for new add-ons**.
4. Install **EVCC Test Token Auto-Sync** from the **Local add-ons** section.

---

### Method 3: Standalone Shell Script & Automation

If you run Home Assistant Core / Container or prefer not using an add-on:

1. Copy `standalone/evcc_token_sync.py` to `/config/scripts/evcc_token_sync.py`.
2. Add the shell command in your `configuration.yaml`:
   ```yaml
   shell_command:
     sync_evcc_token: "python3 /config/scripts/evcc_token_sync.py --evcc-url http://a0d7b954-evcc:7070"
   ```
3. Add the scheduled automation in `automations.yaml`:
   ```yaml
   - id: "evcc_auto_renew_token"
     alias: "EVCC - Auto Renew Test Token"
     trigger:
       - platform: time
         at: "03:00:00"
       - platform: homeassistant
         event: start
     action:
       - service: shell_command.sync_evcc_token
     mode: single
   ```

---

## Add-on Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `evcc_url` | string | `http://a0d7b954-evcc:7070` | Endpoint of your EVCC instance |
| `check_interval_hours` | int | `6` | Interval between renewal checks |
| `notify_ha` | bool | `true` | Send persistent notification in HA on renewal |

---

## License

MIT License. See [LICENSE](LICENSE) for details.
