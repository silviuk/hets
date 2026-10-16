#!/usr/bin/env python3
"""
Standalone EVCC Test Token Auto-Sync
Can be run via cron, AppDaemon, or Home Assistant shell_command.
Uses pure standard library. Implements Alternative B with verification.
"""

import argparse
import base64
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hets_standalone")

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


def fetch_latest_token() -> tuple[str | None, int]:
    jwt_regex = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")
    best_token = None
    latest_exp = 0

    for url in SOURCES:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "HomeAssistant-HETS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                text = resp.read().decode("utf-8", errors="ignore")

            matches = jwt_regex.findall(text)
            for candidate in matches:
                payload = decode_jwt_payload(candidate)
                exp = payload.get("exp", 0)
                if exp > latest_exp:
                    latest_exp = exp
                    best_token = candidate
        except Exception as err:
            logger.debug("Error checking source %s: %s", url, err)

    return best_token, latest_exp


def evcc_login(base_url: str, password: str | None) -> str | None:
    if not password:
        return None

    login_url = f"{base_url.rstrip('/')}/api/auth/login"
    login_payload = json.dumps({"password": password}).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=login_payload,
        headers={"Content-Type": "application/json", "User-Agent": "HomeAssistant-HETS/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            for header in resp.headers.get_all("Set-Cookie", []):
                if "auth=" in header:
                    return header.split(";")[0].strip()
    except Exception as err:
        logger.debug("Login failed at %s: %s", login_url, err)
    return None


def verify_token_instated(base_url: str, cookie: str | None) -> bool:
    headers = {"User-Agent": "HomeAssistant-HETS/1.0"}
    if cookie:
        headers["Cookie"] = cookie

    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/state", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("result", {})
            sponsor = result.get("sponsor", {})
            status = sponsor.get("status", {})
            name = status.get("name") or result.get("sponsorToken")
            if name and name != "sponsorship unavailable":
                logger.info("VERIFICATION CONFIRMED: Token is actively instated in EVCC! Sponsor: '%s', Expires: %s", name, status.get("expiresAt", "N/A"))
                return True
    except Exception as err:
        logger.debug("Verification request failed: %s", err)
    return False


def push_token_to_evcc(evcc_url: str, token: str, password: str | None = None) -> bool:
    base_url = evcc_url.rstrip("/")
    endpoint = f"{base_url}/api/config/sponsortoken"
    cookie = evcc_login(base_url, password)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "HomeAssistant-HETS/1.0",
    }
    if cookie:
        headers["Cookie"] = cookie

    data = json.dumps({"token": token}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status in [200, 204]:
                logger.info("EVCC API accepted token at %s", endpoint)
                time.sleep(1)
                return verify_token_instated(base_url, cookie)
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        logger.error("EVCC API rejected request at %s (HTTP %s): %s", endpoint, err.code, err_msg)
    except Exception as err:
        logger.error("Failed connection to %s: %s", endpoint, err)

    return False


def main():
    parser = argparse.ArgumentParser(description="EVCC Test Token Sync Script (HETS)")
    parser.add_argument(
        "--evcc-url",
        default="http://localhost:7070",
        help="URL of the EVCC instance (default: http://localhost:7070)",
    )
    parser.add_argument(
        "--evcc-password",
        default="",
        help="Admin password for EVCC if authentication is enabled",
    )
    args = parser.parse_args()

    token, exp = fetch_latest_token()
    if not token:
        logger.error("No valid sponsor token found.")
        sys.exit(1)

    exp_str = (
        datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if exp
        else "unknown"
    )
    logger.info("Found token with expiration: %s", exp_str)

    if push_token_to_evcc(args.evcc_url, token, args.evcc_password):
        logger.info("EVCC token successfully updated and verified!")
    else:
        logger.error("Could not update/verify EVCC token.")
        sys.exit(2)


if __name__ == "__main__":
    main()
