#!/usr/bin/env python3
"""
HETS (EVCC Test Token Auto-Sync)
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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hets_sync")

OPTIONS_PATH = "/data/options.json"
CONFIG = {
    "evcc_url": "http://127.0.0.1:7070",
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
    jwt_regex = re.compile(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+")
    best_token = None
    latest_exp = 0

    for url in SOURCES:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "HomeAssistant-HETS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="ignore")

            matches = jwt_regex.findall(text)
            for candidate in matches:
                payload = decode_jwt_payload(candidate)
                exp = payload.get("exp", 0)
                if exp > latest_exp:
                    latest_exp = exp
                    best_token = candidate
                    logger.debug("Found candidate token in %s (exp: %s)", url, exp)
        except Exception as err:
            logger.debug("Error checking source %s: %s", url, err)

    return best_token


def discover_evcc_from_supervisor() -> list[str]:
    """Queries Home Assistant Supervisor to auto-discover EVCC add-on hostname/slug."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        return []

    discovered = []
    try:
        req = urllib.request.Request(
            "http://supervisor/addons",
            headers={
                "Authorization": f"Bearer {supervisor_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            addons = data.get("data", {}).get("addons", [])
            for addon in addons:
                slug = addon.get("slug", "")
                name = addon.get("name", "")
                if "evcc" in slug.lower() or "evcc" in name.lower():
                    logger.info("Discovered EVCC add-on in Supervisor: slug='%s', state='%s'", slug, addon.get("state"))
                    discovered.append(f"http://{slug}:7070")
                    discovered.append(f"http://{slug.replace('_', '-')}:7070")
                    discovered.append(f"http://{slug.replace('-', '_')}:7070")
    except Exception as err:
        logger.debug("Supervisor add-on query notice: %s", err)

    return discovered


def get_candidate_evcc_urls() -> list[str]:
    """Builds a prioritized list of EVCC candidate endpoints."""
    configured = CONFIG.get("evcc_url", "").strip().rstrip("/")
    candidates = []

    if configured:
        candidates.append(configured)

    # Add discovered supervisor add-on hostnames
    candidates.extend(discover_evcc_from_supervisor())

    # Fallback host network and local addresses
    candidates.extend([
        "http://127.0.0.1:7070",
        "http://localhost:7070",
        "http://homeassistant.local:7070",
        "http://homeassistant:7070",
        "http://a0d7b954-evcc:7070",
        "http://a0d7b954_evcc:7070",
        "http://evcc:7070",
        "http://local-evcc:7070",
    ])

    # Deduplicate while preserving order
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def get_current_evcc_token() -> str | None:
    """Fetches the currently active sponsor token from EVCC API."""
    for base_url in get_candidate_evcc_urls():
        try:
            req = urllib.request.Request(
                f"{base_url}/api/state",
                headers={"User-Agent": "HomeAssistant-HETS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result = data.get("result", {})
                token = result.get("sponsorToken") or result.get("sponsortoken")
                if token is not None:
                    return token
        except Exception:
            continue
    return None


def push_token_to_evcc(token: str) -> bool:
    """Posts the new token to EVCC configuration endpoint across candidate URLs."""
    candidate_urls = get_candidate_evcc_urls()
    errors = []

    for base_url in candidate_urls:
        endpoint = f"{base_url}/api/sponsortoken"
        payloads = [{"token": token}, {"sponsortoken": token}]

        for payload in payloads:
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    endpoint,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "HomeAssistant-HETS/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status in [200, 204]:
                        logger.info("Successfully connected and pushed token to EVCC at %s", endpoint)
                        CONFIG["evcc_url"] = base_url
                        return True
            except urllib.error.HTTPError as err:
                if err.code in [200, 204]:
                    logger.info("Successfully pushed token to EVCC at %s (HTTP %s)", endpoint, err.code)
                    CONFIG["evcc_url"] = base_url
                    return True
                errors.append(f"{endpoint} -> HTTP {err.code}: {err.reason}")
            except Exception as err:
                errors.append(f"{endpoint} -> {err}")

    logger.error("Could not reach EVCC at any candidate endpoints:")
    for err in errors[:5]:  # Log first 5 failure reasons
        logger.error("  - %s", err)
    logger.info("TIP: In HETS App Configuration, set 'evcc_url' to your exact EVCC IP:port (e.g. http://192.168.1.50:7070)")
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
        body = json.dumps({
            "title": title,
            "message": message,
            "notification_id": "evcc_test_token_sync",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {supervisor_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
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
        logger.info("EVCC sponsor token updated successfully!")
        send_ha_notification(
            title="EVCC Sponsor Token Renewed",
            message=f"EVCC test sponsor token has been automatically renewed.\n\n**Valid until:** {exp_str}",
        )
        return latest_token
    else:
        logger.error("Failed to apply new token to EVCC. Check that EVCC is running.")
        return last_token


def main():
    logger.info("Starting EVCC Test Token Auto-Sync service (HETS)")
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
