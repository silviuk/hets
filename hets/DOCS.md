# Home Assistant App: HETS (EVCC Test Token Auto-Sync)

**HETS** is an automated Home Assistant App that periodically checks the official EVCC documentation and sponsorship endpoints for renewed test/trial sponsor tokens and updates your EVCC instance via its live REST API.

## Configuration

In the App configuration tab, configure the following options:

### `evcc_url`
The URL where your EVCC instance is accessible.
- If using the official EVCC Home Assistant App: `http://a0d7b954-evcc:7070`
- If using EVCC locally: `http://localhost:7070` or `http://homeassistant.local:7070`

### Renewal timing
The App reads the current token from disk on startup and waits until one hour before its expiry before checking upstream. If no current token or expiry is available, it checks immediately. During the renewal window it checks every two minutes, then backs off to ten-minute checks from 15 minutes past expiry and thirty-minute checks from one hour past expiry. After applying a replacement, it waits until that token's renewal window.

### `notify_ha`
Whether to send a Home Assistant persistent notification whenever a new token is retrieved and pushed to EVCC (`true` or `false`).
