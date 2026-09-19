# Home Assistant Add-on: EVCC Test Token Auto-Sync

Automatically checks the official EVCC documentation and sponsorship endpoints for renewed test/trial sponsor tokens and updates your EVCC instance via its live REST API.

## Configuration

In the add-on configuration tab, configure the following options:

### `evcc_url`
The URL where your EVCC instance is accessible.
- If using the official EVCC Home Assistant Add-on: `http://a0d7b954-evcc:7070`
- If using EVCC locally: `http://localhost:7070` or `http://homeassistant.local:7070`

### `check_interval_hours`
How often (in hours) the add-on should check for renewed tokens. Default is `6`.

### `notify_ha`
Whether to send a Home Assistant persistent notification whenever a new token is retrieved and pushed to EVCC (`true` or `false`).
