import os
import time
import logging
import ipaddress
from typing import Dict, Set

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =========================
# ENV
# =========================

CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_ZONE_ID = os.getenv("CF_ZONE_ID")
CF_RECORD_NAME = os.getenv("CF_RECORD_NAME")

XRAY_CHECKER_URL = os.getenv("XRAY_CHECKER_URL", "http://xray-checker:2112").rstrip("/")
XRAY_CHECKER_USER = os.getenv("XRAY_CHECKER_USER")
XRAY_CHECKER_PASS = os.getenv("XRAY_CHECKER_PASS")

MAX_LATENCY_MS = int(os.getenv("MAX_LATENCY_MS", "1200"))
MIN_ALIVE_IPS = int(os.getenv("MIN_ALIVE_IPS", "1"))

SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL", "60"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))

PROXIED = os.getenv("PROXIED", "false").lower() == "true"
TTL = int(os.getenv("TTL", "60"))

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
DELETE_STALE_RECORDS = os.getenv("DELETE_STALE_RECORDS", "true").lower() == "true"

CF_API_BASE = "https://api.cloudflare.com/client/v4"


# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("cf-xray-sync")


# =========================
# HTTP SESSION
# =========================

def make_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "DELETE"),
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


session = make_session()


# =========================
# VALIDATION
# =========================

def require_env() -> None:
    required = {
        "CF_API_TOKEN": CF_API_TOKEN,
        "CF_ZONE_ID": CF_ZONE_ID,
        "CF_RECORD_NAME": CF_RECORD_NAME,
        "XRAY_CHECKER_USER": XRAY_CHECKER_USER,
        "XRAY_CHECKER_PASS": XRAY_CHECKER_PASS,
    }

    missing = [key for key, value in required.items() if not value]

    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def cf_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }


# =========================
# XRAY CHECKER
# =========================

def get_alive_xray_ips() -> Set[str]:
    url = f"{XRAY_CHECKER_URL}/api/v1/proxies"

    log.info("Fetching proxies from xray-checker: %s", url)

    response = session.get(
        url,
        auth=HTTPBasicAuth(XRAY_CHECKER_USER, XRAY_CHECKER_PASS),
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    payload = response.json()
    proxies = payload.get("data", [])

    alive_ips: Set[str] = set()

    for proxy in proxies:
        online = proxy.get("online")
        latency = proxy.get("latencyMs")
        server = proxy.get("server")

        if latency is None:
            latency = 999999

        if not online:
            continue

        if latency > MAX_LATENCY_MS:
            log.info("Skip slow proxy: %s latency=%sms", server, latency)
            continue

        if not server:
            continue

        try:
            ipaddress.ip_address(server)
            alive_ips.add(server)
        except ValueError:
            log.warning("Skip non-IP server: %s", server)

    return alive_ips


# =========================
# CLOUDFLARE
# =========================

def get_cloudflare_a_records() -> Dict[str, str]:
    url = f"{CF_API_BASE}/zones/{CF_ZONE_ID}/dns_records"

    params = {
        "type": "A",
        "name": CF_RECORD_NAME,
        "per_page": 100,
    }

    response = session.get(
        url,
        headers=cf_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    result = response.json().get("result", [])

    return {
        record["content"]: record["id"]
        for record in result
        if record.get("type") == "A"
    }


def create_a_record(ip: str) -> None:
    url = f"{CF_API_BASE}/zones/{CF_ZONE_ID}/dns_records"

    payload = {
        "type": "A",
        "name": CF_RECORD_NAME,
        "content": ip,
        "ttl": TTL,
        "proxied": PROXIED,
    }

    if DRY_RUN:
        log.info("[DRY-RUN] Would add: %s -> %s", CF_RECORD_NAME, ip)
        return

    response = session.post(
        url,
        headers=cf_headers(),
        json=payload,
        timeout=HTTP_TIMEOUT,
    )

    if not response.ok:
        log.error("Failed to add %s: %s", ip, response.text)
        return

    log.info("Added: %s -> %s", CF_RECORD_NAME, ip)


def delete_a_record(record_id: str, ip: str) -> None:
    url = f"{CF_API_BASE}/zones/{CF_ZONE_ID}/dns_records/{record_id}"

    if DRY_RUN:
        log.info("[DRY-RUN] Would delete: %s -> %s", CF_RECORD_NAME, ip)
        return

    response = session.delete(
        url,
        headers=cf_headers(),
        timeout=HTTP_TIMEOUT,
    )

    if not response.ok:
        log.error("Failed to delete %s: %s", ip, response.text)
        return

    log.info("Deleted: %s -> %s", CF_RECORD_NAME, ip)


# =========================
# SYNC
# =========================

def sync_once() -> None:
    log.info("=== Xray Checker -> Cloudflare DNS sync ===")

    alive_ips = get_alive_xray_ips()

    if len(alive_ips) < MIN_ALIVE_IPS:
        log.error(
            "Alive IP count is too low: %s. Minimum required: %s. DNS changes skipped.",
            len(alive_ips),
            MIN_ALIVE_IPS,
        )
        return

    current_records = get_cloudflare_a_records()
    current_ips = set(current_records.keys())

    to_add = alive_ips - current_ips
    to_delete = current_ips - alive_ips

    log.info("Alive Xray IPs: %s", sorted(alive_ips))
    log.info("Current CF IPs: %s", sorted(current_ips))
    log.info("To add: %s", sorted(to_add))
    log.info("To delete: %s", sorted(to_delete))

    for ip in sorted(to_add):
        create_a_record(ip)

    if DELETE_STALE_RECORDS:
        for ip in sorted(to_delete):
            delete_a_record(current_records[ip], ip)
    else:
        log.info("DELETE_STALE_RECORDS=false, stale records will not be deleted.")


def main() -> None:
    require_env()

    log.info("Started cf-xray-sync")
    log.info("DNS record: %s", CF_RECORD_NAME)
    log.info("Xray checker: %s", XRAY_CHECKER_URL)
    log.info("Max latency: %sms", MAX_LATENCY_MS)
    log.info("Sync interval: %ss", SYNC_INTERVAL)
    log.info("Dry run: %s", DRY_RUN)

    while True:
        try:
            sync_once()
        except Exception as error:
            log.exception("Sync failed: %s", error)

        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()