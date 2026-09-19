#!/usr/bin/env python3
"""
EVCC Test Token Auto-Sync
Automatically checks for renewed/updated EVCC trial sponsor tokens
and updates the local EVCC instance.
"""

import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evcc_token_sync")

OPTIONS_PATH = "/data/options.json"
CONFIG = {
    "evcc_url": "http://a0d7b954-evcc:7070",
    "check_interval_hours": 6,
    "notify_ha": True,
}

if os.path.exists(OPTIONS_PATH):
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            CONFIG.update(json.load(f))
        logger.info("Loaded configuration from %s", OPTIONS_PATH)
    except Exception as err:
        logger.warning("Could not parse options file: %s. Using defaults.", err)

SOURCES = [
    "https://raw.githubusercontent.com/evcc-io/docs/main/docs/sponsorship.md",
    "https://raw.githubusercontent.com/evcc-io/docs/main/i18n/de/docusaurus-plugin-content-docs/current/sponsorship.md",
    "https://docs.evcc.io/en/sponsorship",
    "https://docs.evcc.io/de/sponsorship",
    "https://sponsor.evcc.io/",
]


def decode_jwt_payload(token: str) -> dict:
    """Safely decodes unverified JWT payload."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
        return json.loads(decoded.decode("utf-8", errors="ignore"))
    except Exception as err:
        logger.debug("Failed decoding JWT segment: %s", err)
        return {}


def fetch_latest_token() -> str | None:
    """Scrapes candidate sources for the latest valid sponsor JWT."""
    headers = {"User-Agent": "HomeAssistant-EVCCTokenSync/1.0"}
    jwt_regex = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")

    best_token = None
    latest_exp = 0

    for url in SOURCES:
        try:
            logger.debug("Checking source URL: %s", url)
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            matches = jwt_regex.findall(resp.text)
            for candidate in matches:
                payload = decode_jwt_payload(candidate)
                # Ensure the payload contains typical sponsor token claims
                exp = payload.get("exp", 0)
                if exp > latest_exp:
                    latest_exp = exp
                    best_token = candidate
                    logger.debug("Found candidate token in %s (exp: %s)", url, exp)
        except Exception as err:
            logger.debug("Error checking source %s: %s", url, err)

    return best_token


def get_current_evcc_token() -> str | None:
    """Fetches the currently active sponsor token or status from EVCC API."""
    evcc_url = CONFIG.get("evcc_url", "").rstrip("/")
    try:
        resp = requests.get(f"{evcc_url}/api/state", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            return result.get("sponsorToken") or result.get("sponsortoken")
    except Exception as err:
        logger.debug("Could not fetch current state from EVCC: %s", err)
    return None


def push_token_to_evcc(token: str) -> bool:
    """Posts the new token to EVCC configuration endpoint."""
    evcc_url = CONFIG.get("evcc_url", "").rstrip("/")
    endpoint = f"{evcc_url}/api/sponsortoken"

    payloads = [{"token": token}, {"sponsortoken": token}]
    for payload in payloads:
        try:
            res = requests.post(endpoint, json=payload, timeout=10)
            if res.status_code in [200, 204]:
                return True
        except Exception as err:
            logger.error("Error sending token to %s: %s", endpoint, err)

    return False


def send_ha_notification(title: str, message: str) -> None:
    """Sends a persistent notification via Home Assistant Supervisor API."""
    if not CONFIG.get("notify_ha", True):
        return

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        logger.debug("SUPERVISOR_TOKEN not set; skipping Home Assistant notification")
        return

    try:
        url = "http://supervisor/core/api/services/persistent_notification/create"
        headers = {
            "Authorization": f"Bearer {supervisor_token}",
            "Content-Type": "application/json",
        }
        body = {
            "title": title,
            "message": message,
            "notification_id": "evcc_test_token_sync",
        }
        requests.post(url, headers=headers, json=body, timeout=10)
    except Exception as err:
        logger.warning("Failed sending HA persistent notification: %s", err)


def run_sync_cycle(last_token: str | None) -> str | None:
    logger.info("Scanning official sources for updated test sponsor tokens...")
    latest_token = fetch_latest_token()

    if not latest_token:
        logger.warning("No candidate sponsor token found across sources.")
        return last_token

    payload = decode_jwt_payload(latest_token)
    exp_ts = payload.get("exp")
    exp_str = (
        datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if exp_ts
        else "Unknown"
    )

    current_evcc_token = get_current_evcc_token()

    if latest_token == last_token or (current_evcc_token and latest_token == current_evcc_token):
        logger.info("EVCC token is up to date (Expires: %s).", exp_str)
        return latest_token

    logger.info("New or renewed token detected! Expiration: %s", exp_str)
    if push_token_to_evcc(latest_token):
        logger.info("Successfully updated EVCC sponsor token via API!")
        send_ha_notification(
            title="EVCC Sponsor Token Renewed",
            message=f"EVCC test sponsor token has been automatically renewed.\n\n**Valid until:** {exp_str}",
        )
        return latest_token
    else:
        logger.error("Failed to apply new token to EVCC.")
        return last_token


def main():
    logger.info("Starting EVCC Test Token Auto-Sync service")
    logger.info("Configured EVCC URL: %s", CONFIG.get("evcc_url"))
    interval_hours = max(1, int(CONFIG.get("check_interval_hours", 6)))
    logger.info("Check interval: every %d hours", interval_hours)

    last_token = None
    while True:
        try:
            last_token = run_sync_cycle(last_token)
        except Exception as err:
            logger.error("Unexpected error during sync cycle: %s", err, exc_info=True)

        time.sleep(interval_hours * 3600)


if __name__ == "__main__":
    main()
