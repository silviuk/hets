#!/usr/bin/env python3
"""
Standalone EVCC Test Token Auto-Sync
Can be run via cron, AppDaemon, or Home Assistant shell_command.
"""

import argparse
import base64
import json
import logging
import re
import sys
from datetime import datetime, timezone
import requests

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("evcc_token_sync")

SOURCES = [
    "https://raw.githubusercontent.com/evcc-io/docs/main/docs/sponsorship.md",
    "https://raw.githubusercontent.com/evcc-io/docs/main/i18n/de/docusaurus-plugin-content-docs/current/sponsorship.md",
    "https://docs.evcc.io/en/sponsorship",
    "https://docs.evcc.io/de/sponsorship",
    "https://sponsor.evcc.io/",
]


def decode_jwt_payload(token: str) -> dict:
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
    headers = {"User-Agent": "HomeAssistant-EVCCTokenSync/1.0"}
    jwt_regex = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")

    best_token = None
    latest_exp = 0

    for url in SOURCES:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            matches = jwt_regex.findall(resp.text)
            for candidate in matches:
                payload = decode_jwt_payload(candidate)
                exp = payload.get("exp", 0)
                if exp > latest_exp:
                    latest_exp = exp
                    best_token = candidate
        except Exception as err:
            logger.debug("Error checking source %s: %s", url, err)

    return best_token


def push_token_to_evcc(evcc_url: str, token: str) -> bool:
    endpoint = f"{evcc_url.rstrip('/')}/api/sponsortoken"
    for payload in [{"token": token}, {"sponsortoken": token}]:
        try:
            res = requests.post(endpoint, json=payload, timeout=10)
            if res.status_code in [200, 204]:
                return True
        except Exception as err:
            logger.error("Failed pushing to %s: %s", endpoint, err)
    return False


def main():
    parser = argparse.ArgumentParser(description="EVCC Test Token Sync Script")
    parser.add_argument(
        "--evcc-url",
        default="http://localhost:7070",
        help="URL of the EVCC instance (default: http://localhost:7070)",
    )
    args = parser.parse_args()

    token = fetch_latest_token()
    if not token:
        logger.error("No valid sponsor token found.")
        sys.exit(1)

    payload = decode_jwt_payload(token)
    exp = payload.get("exp")
    exp_str = (
        datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if exp
        else "unknown"
    )
    logger.info("Found token with expiration: %s", exp_str)

    if push_token_to_evcc(args.evcc_url, token):
        logger.info("EVCC token successfully updated!")
    else:
        logger.error("Could not update EVCC token.")
        sys.exit(2)


if __name__ == "__main__":
    main()
