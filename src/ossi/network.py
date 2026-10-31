"""Checks this computer's connection, and what's on the home network, using Windows' own tools.

These functions can take seconds, so callers must not run them on the window's thread.
"""

import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

# Public addresses that answer pings. Asking two means one having a bad day isn't an outage.
INTERNET_HOSTS = ("1.1.1.1", "8.8.8.8")
PING_TIMEOUT_MS = 1000
SCAN_TIMEOUT_MS = 500
SCAN_WORKERS = 64
NAME_WORKERS = 16
RUN_TIMEOUT_SECONDS = 15
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

DEFAULT_ROUTE = re.compile(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+\S+\s+(\d+)\s*$", re.M)
REPLY_TIME = re.compile(r"[=<](\d+)\s*ms\s+TTL=", re.I)
ARP_ROW = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+((?:[0-9a-f]{2}-){5}[0-9a-f]{2})\s", re.I | re.M)
NOT_A_DEVICE = ("ff-ff-ff-ff-ff-ff", "01-00-5e")  # Addresses for reaching many devices at once.


@dataclass
class Status:
    connected: bool  # Whether this computer has a route to any network.
    router_ms: float | None = None
    internet_ms: float | None = None


def run(*command):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=RUN_TIMEOUT_SECONDS,
        creationflags=NO_WINDOW,
    ).stdout


def parse_gateway(route_output):
    """The router's address from `route print`, choosing the connection Windows prefers."""
    routes = DEFAULT_ROUTE.findall(route_output)
    return min(routes, key=lambda route: int(route[1]))[0] if routes else None


def gateway():
    return parse_gateway(run("route", "print", "-4", "0.0.0.0"))


def parse_ping(output):
    """How long a reply took, or None. "TTL=" means a real reply, not "host unreachable"."""
    if "TTL=" not in output.upper():
        return None
    match = REPLY_TIME.search(output)
    return float(match.group(1)) if match else 0.0


def ping(host, timeout_ms=PING_TIMEOUT_MS):
    return parse_ping(run("ping", "-n", "1", "-w", str(timeout_ms), host))


def check():
    router = gateway()
    if not router:
        return Status(connected=False)
    internet = next(
        (ms for ms in map(ping, INTERNET_HOSTS) if ms is not None),
        None,
    )
    return Status(True, ping(router), internet)


def is_unicast(mac):
    return not mac.lower().startswith(NOT_A_DEVICE)


def parse_arp(output, network):
    """The devices Windows has seen on this network, as (address, hardware address) pairs."""
    found = {}
    for address, mac in ARP_ROW.findall(output):
        if ipaddress.ip_address(address) in network and is_unicast(mac):
            found[address] = mac.lower()
    return found


def name_of(address):
    try:
        return socket.gethostbyaddr(address)[0]
    except OSError:
        return ""


def find_devices():
    """Everything answering on the home network, as a list of dicts. None if not on one."""
    router = gateway()
    if not router or not ipaddress.ip_address(router).is_private:
        return None
    network = ipaddress.ip_network(f"{router}/24", strict=False)
    # Asking each address makes Windows note the devices that answer, even ones that ignore pings.
    with ThreadPoolExecutor(SCAN_WORKERS) as workers:
        list(workers.map(lambda host: ping(str(host), SCAN_TIMEOUT_MS), network.hosts()))
    seen = parse_arp(run("arp", "-a"), network)
    with ThreadPoolExecutor(NAME_WORKERS) as workers:
        names = list(workers.map(name_of, seen))
    return [
        {"address": address, "mac": mac, "name": name, "is_router": address == router}
        for (address, mac), name in zip(seen.items(), names)
    ]
