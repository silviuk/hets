#!/usr/bin/env python3
"""
HETS (EVCC Test Token Auto-Sync)
Intelligently syncs EVCC test sponsor tokens just before expiration.
Attempts proper Alternative B (authenticated API session + verification) first,
with automated SQLite database / YAML fallback if rejected by cloud check.
"""

import base64
import glob
import json
import logging
import os
import re
import socket
import sqlite3
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
    "evcc_password": "",
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


def discover_evcc_from_supervisor() -> tuple[list[str], str | None]:
    """Queries Home Assistant Supervisor to auto-discover EVCC add-on hostnames and slug."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        return [], None

    discovered = []
    evcc_slug = None
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
                    evcc_slug = slug
                    logger.info("Discovered EVCC in Supervisor: slug='%s', state='%s'", slug, addon.get("state"))
                    discovered.append(f"http://{slug}:7070")
                    discovered.append(f"http://{slug.replace('_', '-')}:7070")
                    discovered.append(f"http://{slug.replace('-', '_')}:7070")
    except Exception as err:
        logger.debug("Supervisor add-on query notice: %s", err)

    return discovered, evcc_slug


def get_candidate_evcc_urls() -> list[str]:
    """Builds a prioritized list of EVCC candidate endpoints."""
    configured = CONFIG.get("evcc_url", "").strip().rstrip("/")
    candidates = []

    if configured:
        candidates.append(configured)

    lan_ips = detect_host_lan_ips()
    for ip in lan_ips:
        candidates.append(f"http://{ip}:7070")

    discovered, _ = discover_evcc_from_supervisor()
    candidates.extend(discovered)

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


def evcc_login(base_url: str) -> str | None:
    """
    Logs in to EVCC via POST /api/auth/login if evcc_password is configured.
    Returns the auth session cookie string (e.g. 'auth=ey...') or None.
    """
    password = CONFIG.get("evcc_password", "").strip()
    if not password:
        return None

    login_url = f"{base_url}/api/auth/login"
    login_payload = json.dumps({"password": password}).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=login_payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "HomeAssistant-HETS/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            for header in resp.headers.get_all("Set-Cookie", []):
                if "auth=" in header:
                    cookie = header.split(";")[0].strip()
                    logger.info("Successfully authenticated with EVCC on %s", base_url)
                    return cookie
    except urllib.error.HTTPError as err:
        logger.warning("EVCC login failed at %s (HTTP %s): %s", login_url, err.code, err.reason)
    except Exception as err:
        logger.debug("EVCC login connection error to %s: %s", login_url, err)

    return None


def verify_token_instated(base_url: str, cookie: str | None = None) -> tuple[bool, str, str]:
    """
    Queries GET /api/state to verify that the token has been accepted and instated in EVCC.
    Returns (is_instated, sponsor_name, expires_at).
    """
    headers = {"User-Agent": "HomeAssistant-HETS/1.0"}
    if cookie:
        headers["Cookie"] = cookie

    state_url = f"{base_url}/api/state"
    try:
        req = urllib.request.Request(state_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result = data.get("result", {})
            sponsor = result.get("sponsor", {})
            status = sponsor.get("status", {})
            name = status.get("name") or result.get("sponsorToken")
            expires_at = status.get("expiresAt", "")
            if name and name != "sponsorship unavailable":
                return True, str(name), str(expires_at)
            elif name == "sponsorship unavailable":
                logger.warning("EVCC reported sponsor status: 'sponsorship unavailable'")
    except Exception as err:
        logger.debug("Verification request failed against %s: %s", state_url, err)

    return False, "", ""


def push_token_via_api(token: str) -> bool:
    """
    Alternative B:
    Performs authenticated session login and calls POST /api/config/sponsortoken.
    Validates that the token was genuinely instated and recognized by EVCC.
    """
    candidate_urls = get_candidate_evcc_urls()

    for base_url in candidate_urls:
        endpoint = f"{base_url}/api/config/sponsortoken"
        cookie = evcc_login(base_url)

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
                    resp_body = resp.read().decode("utf-8", errors="ignore")
                    logger.info("EVCC API accepted token at %s. Response: %s", endpoint, resp_body)

                    # Verify that the token is instated and active in system state
                    time.sleep(1)
                    instated, name, expires_at = verify_token_instated(base_url, cookie)
                    if instated:
                        logger.info("VERIFICATION CONFIRMED: Token is actively instated in EVCC! Sponsor: '%s', Expires: %s", name, expires_at)
                        CONFIG["evcc_url"] = base_url
                        return True
                    else:
                        logger.warning("API returned HTTP 200, but EVCC state verification did not report active sponsorship.")
                        # Check response body for status fields
                        try:
                            resp_json = json.loads(resp_body)
                            if resp_json.get("name") and resp_json.get("name") != "sponsorship unavailable":
                                logger.info("VERIFICATION CONFIRMED from API response payload: Sponsor: '%s'", resp_json.get("name"))
                                CONFIG["evcc_url"] = base_url
                                return True
                        except Exception:
                            pass
        except urllib.error.HTTPError as err:
            err_msg = err.read().decode("utf-8", errors="ignore")
            logger.warning("EVCC API rejected request at %s (HTTP %s): %s", endpoint, err.code, err_msg)
            if err.code == 401:
                logger.info("NOTE: EVCC requires an admin password. Please set 'evcc_password' in HETS configuration.")
        except Exception as err:
            logger.debug("Failed connection to %s: %s", endpoint, err)

    return False


def find_evcc_db_paths() -> list[str]:
    """Finds all potential evcc.db SQLite database locations on the Home Assistant disk."""
    patterns = [
        "/addon_configs/*evcc*/evcc.db",
        "/addon_configs/evcc/evcc.db",
        "/data/evcc.db",
        "/config/evcc.db",
        "/share/evcc.db",
    ]
    found = []
    for pat in patterns:
        for match in glob.glob(pat):
            if os.path.isfile(match):
                found.append(match)
    return found


def update_evcc_sqlite_db(token: str) -> bool:
    """
    Direct SQLite update in evcc.db.
    Inserts or replaces 'sponsorToken' in the 'settings' table.
    """
    db_files = find_evcc_db_paths()
    if not db_files:
        logger.debug("No accessible evcc.db files found.")
        return False

    success = False
    for db_path in db_files:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
            if cursor.fetchone():
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES ('sponsorToken', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (token,),
                )
                conn.commit()
                logger.info("Successfully updated 'sponsorToken' in SQLite database: %s", db_path)
                success = True
            conn.close()
        except Exception as err:
            logger.error("Error writing to SQLite DB %s: %s", db_path, err)

    return success


def find_evcc_yaml_paths() -> list[str]:
    """Finds all potential evcc.yaml file locations on the Home Assistant disk."""
    patterns = [
        "/addon_configs/*evcc*/evcc.yaml",
        "/addon_configs/evcc.yaml",
        "/config/evcc.yaml",
        "/share/evcc.yaml",
        "/homeassistant/evcc.yaml",
    ]
    found = []
    for pat in patterns:
        for match in glob.glob(pat):
            if os.path.isfile(match):
                found.append(match)
    return found


def read_existing_token_from_disk() -> tuple[str | None, int]:
    """Reads the current token configured in evcc.db or evcc.yaml if accessible."""
    # First check evcc.db as it takes precedence
    for db_path in find_evcc_db_paths():
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key='sponsorToken'")
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                token = row[0].strip()
                payload = decode_jwt_payload(token)
                exp = payload.get("exp", 0)
                logger.info("Read active token from SQLite DB %s (Expires: %s)", db_path, format_timestamp(exp))
                return token, exp
        except Exception as err:
            logger.debug("Could not read SQLite %s: %s", db_path, err)

    # Next check evcc.yaml
    for ypath in find_evcc_yaml_paths():
        try:
            with open(ypath, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"(?m)^\s*sponsortoken:\s*['\"]?([a-zA-Z0-9_\-\.]+)['\"]?", content)
            if match:
                token = match.group(1).strip()
                payload = decode_jwt_payload(token)
                exp = payload.get("exp", 0)
                logger.info("Read existing token from YAML %s (Expires: %s)", ypath, format_timestamp(exp))
                return token, exp
        except Exception as err:
            logger.debug("Could not read from %s: %s", ypath, err)
    return None, 0


def update_evcc_yaml_and_restart(token: str) -> bool:
    """
    Fallback method: Direct file update on evcc.yaml + evcc.db + Supervisor add-on restart.
    """
    yaml_files = find_evcc_yaml_paths()
    updated_yaml = False

    for ypath in yaml_files:
        try:
            with open(ypath, "r", encoding="utf-8") as f:
                content = f.read()

            if "sponsortoken:" in content:
                new_content = re.sub(
                    r"(?m)^(\s*sponsortoken:\s*).*$",
                    rf"\g<1>{token}",
                    content,
                )
            else:
                new_content = f"sponsortoken: {token}\n" + content

            if new_content != content:
                with open(ypath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                logger.info("Updated sponsortoken in %s", ypath)
                updated_yaml = True
        except Exception as err:
            logger.error("Error writing to %s: %s", ypath, err)

    # Also update SQLite DB so database precedence aligns with YAML
    updated_db = update_evcc_sqlite_db(token)

    if not updated_yaml and not updated_db:
        logger.debug("No YAML or SQLite DB files could be updated.")
        return False

    # Restart EVCC Add-on via Supervisor API to load new YAML & DB
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    _, evcc_slug = discover_evcc_from_supervisor()

    if supervisor_token and evcc_slug:
        try:
            logger.info("Triggering restart of EVCC add-on (%s) via Supervisor...", evcc_slug)
            req = urllib.request.Request(
                f"http://supervisor/addons/{evcc_slug}/restart",
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {supervisor_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                logger.info("EVCC add-on restarted successfully!")
                return True
        except Exception as err:
            logger.warning("Could not restart EVCC add-on automatically: %s", err)
            return True

    return True


def apply_token_to_evcc(token: str) -> bool:
    """
    Applies the token trying Alternative B (API session + verification) first.
    Falls back to direct DB/YAML + restart if API push fails.
    """
    logger.info("==> Trying Alternative B: Live Authenticated REST API...")
    if push_token_via_api(token):
        logger.info("Alternative B succeeded: Token is verified active in EVCC!")
        return True

    logger.warning("Alternative B failed. Falling back to Alternative A: Direct SQLite DB/YAML update + restart...")
    if update_evcc_yaml_and_restart(token):
        logger.info("Alternative A succeeded: Token successfully applied via disk persistence and EVCC restart!")
        return True

    logger.error("All token application methods failed. Check EVCC container status and logs.")
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

    active_token, active_exp = read_existing_token_from_disk()

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

        if latest_token != active_token:
            logger.info("New token detected! (Expires: %s, remaining: %ds). Applying update...", exp_str, seconds_left)
            if apply_token_to_evcc(latest_token):
                active_token = latest_token
                active_exp = latest_exp
                send_ha_notification(
                    title="EVCC Sponsor Token Renewed",
                    message=f"EVCC test sponsor token renewed successfully.\n\n**Valid until:** {exp_str}",
                )
            else:
                logger.error("Failed to apply token to EVCC. Will retry in 60s...")
                time.sleep(60)
                continue
        else:
            logger.info("Token is already up to date and identical to active token (Expires: %s).", exp_str)
            active_exp = latest_exp

        # Re-calculate remaining seconds
        now = int(time.time())
        seconds_left = active_exp - now

        if seconds_left > 300:
            sleep_duration = seconds_left - 240  # 4 mins before expiry
            sleep_duration = min(sleep_duration, 3600)  # Max 1 hr health check
            wake_time = format_timestamp(now + sleep_duration)
            logger.info("Token valid for %d minutes. Sleeping until %s (4 min before expiration).", seconds_left // 60, wake_time)
            time.sleep(sleep_duration)
        else:
            logger.info("Token is near/past expiration (%ds remaining). Actively polling upstream every 60s...", max(0, seconds_left))
            time.sleep(60)


if __name__ == "__main__":
    main()
