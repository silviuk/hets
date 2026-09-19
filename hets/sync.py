#!/usr/bin/env python3
"""
HETS (EVCC Test Token Auto-Sync)
Intelligently syncs EVCC test sponsor tokens just before expiration.
"""

import base64
import json
import logging
import os
import re
import socket
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
    "evcc_url": "",
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


def fetch_latest_token() -> tuple[str | None, int]:
    """Scrapes candidate sources for the latest sponsor JWT and returns (token, exp_timestamp)."""
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


def detect_host_lan_ips() -> list[str]:
    """Detects local LAN/interface IP addresses of the host machine."""
    ips = set()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ":" not in ip:
                ips.add(ip)
    except Exception:
        pass

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if supervisor_token:
        try:
            req = urllib.request.Request(
                "http://supervisor/network/info",
                headers={"Authorization": f"Bearer {supervisor_token}"},
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                interfaces = data.get("data", {}).get("interfaces", [])
                for iface in interfaces:
                    ipv4 = iface.get("ipv4", {})
                    for addr in ipv4.get("address", []):
                        ip_clean = addr.split("/")[0]
                        if ip_clean and not ip_clean.startswith("127."):
                            ips.add(ip_clean)
        except Exception as err:
            logger.debug("Supervisor network info error: %s", err)

    return list(ips)


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
                    logger.info("Discovered EVCC in Supervisor: slug='%s', state='%s'", slug, addon.get("state"))
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

    lan_ips = detect_host_lan_ips()
    for ip in lan_ips:
        candidates.append(f"http://{ip}:7070")

    candidates.extend(discover_evcc_from_supervisor())

    candidates.extend([
        "http://homeassistant.local:7070",
        "http://homeassistant:7070",
        "http://127.0.0.1:7070",
        "http://localhost:7070",
        "http://a0d7b954-evcc:7070",
        "http://a0d7b954_evcc:7070",
        "http://evcc:7070",
    ])

    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def get_current_evcc_token() -> tuple[str | None, int]:
    """Fetches currently configured token from EVCC state API and returns (token, exp_timestamp)."""
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
                if token:
                    payload = decode_jwt_payload(token)
                    return token, payload.get("exp", 0)
        except Exception:
            continue
    return None, 0


def push_token_to_evcc(token: str) -> bool:
    """
    Posts the new token to EVCC configuration endpoints.
    Tries official /api/sponsortoken, /config/sponsortoken, /sponsortoken.
    """
    candidate_urls = get_candidate_evcc_urls()
    api_paths = ["/api/sponsortoken", "/config/sponsortoken", "/sponsortoken", "/api/config/sponsortoken"]
    payload_formats = [
        ("application/json", json.dumps({"token": token}).encode("utf-8")),
        ("application/json", json.dumps({"sponsortoken": token}).encode("utf-8")),
        ("text/plain", token.encode("utf-8")),
    ]

    for base_url in candidate_urls:
        for path in api_paths:
            endpoint = f"{base_url}{path}"
            for content_type, data in payload_formats:
                try:
                    req = urllib.request.Request(
                        endpoint,
                        data=data,
                        headers={
                            "Content-Type": content_type,
                            "User-Agent": "HomeAssistant-HETS/1.0",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        if resp.status in [200, 204]:
                            logger.info("Successfully pushed token to EVCC at %s", endpoint)
                            CONFIG["evcc_url"] = base_url
                            return True
                except urllib.error.HTTPError as err:
                    if err.code in [200, 204]:
                        logger.info("Successfully pushed token to EVCC at %s (HTTP %s)", endpoint, err.code)
                        CONFIG["evcc_url"] = base_url
                        return True
                    logger.debug("HTTP %s from %s", err.code, endpoint)
                except Exception as err:
                    logger.debug("Connection failed to %s: %s", endpoint, err)

    logger.error("Could not reach EVCC at any candidate endpoints.")
    return False


def send_ha_notification(title: str, message: str) -> None:
    """Sends a persistent notification via Home Assistant Supervisor API."""
    if not CONFIG.get("notify_ha", True):
        return

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
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


def format_timestamp(ts: int) -> str:
    if not ts:
        return "Unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    logger.info("Starting HETS (Smart Expiry EVCC Test Token Auto-Sync)")
    lan_ips = detect_host_lan_ips()
    logger.info("Detected Host LAN IPs: %s", lan_ips)

    active_token = None
    active_exp = 0

    while True:
        now = int(time.time())
        logger.info("Checking for EVCC test sponsor token...")
        latest_token, latest_exp = fetch_latest_token()

        if not latest_token:
            logger.warning("No token found from upstream sources. Will retry in 2 minutes.")
            time.sleep(120)
            continue

        exp_str = format_timestamp(latest_exp)
        seconds_left = latest_exp - now

        if latest_token != active_token or seconds_left <= 0:
            logger.info("New/Renewed token found (Expires: %s, remaining: %ds)", exp_str, seconds_left)
            if push_token_to_evcc(latest_token):
                active_token = latest_token
                active_exp = latest_exp
                send_ha_notification(
                    title="EVCC Sponsor Token Renewed",
                    message=f"EVCC test sponsor token renewed successfully.\n\n**Valid until:** {exp_str}",
                )
            else:
                logger.error("Failed to push token to EVCC. Will retry in 60s...")
                time.sleep(60)
                continue
        else:
            logger.info("Current token is active (Expires: %s)", exp_str)

        # Re-calculate remaining seconds
        now = int(time.time())
        seconds_left = active_exp - now

        if seconds_left > 300:
            # Token is valid for more than 5 minutes -> Sleep until 4 minutes before expiry
            sleep_duration = seconds_left - 240  # 4 mins before expiry
            # Cap maximum sleep at 1 hour for regular health checking
            sleep_duration = min(sleep_duration, 3600)
            wake_time = format_timestamp(now + sleep_duration)
            logger.info("Token valid for %d minutes. Sleeping until %s (4 min before expiration).", seconds_left // 60, wake_time)
            time.sleep(sleep_duration)
        else:
            # Within 5 minutes of expiration -> Active poll every 60s for the newly published token
            logger.info("Token is near/past expiration (%ds remaining). Actively polling upstream every 60s...", max(0, seconds_left))
            time.sleep(60)


if __name__ == "__main__":
    main()
