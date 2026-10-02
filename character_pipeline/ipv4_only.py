"""Network workaround for this machine, imported by the pipeline scripts.

* IPv6 is broken here: every connection stalls ~20 s before falling back.
* Hugging Face Space hosts resolve to several load-balancer addresses; from
  this network their responsiveness changes minute to minute (some answer in
  ~0.2 s, others take ~12 s or fail).

Importing this module makes Python resolve IPv4 only. For HTTPS hosts with
several addresses it measures a TLS handshake to each (short timeout, cached
for 30 s) and returns every address, responsive ones fastest first, so
socket.create_connection tries a good address before falling back.
"""

import socket
import ssl
import time

_original_getaddrinfo = socket.getaddrinfo
_ranked: dict[str, tuple[float, dict[str, float]]] = {}
_tls = ssl.create_default_context()
RANK_SECONDS = 30.0


def _handshake_seconds(host: str, ip: str, port: int) -> float:
    start = time.monotonic()
    try:
        # A literal IP resolves to a single address, so this never recurses into ranking.
        with socket.create_connection((ip, port), timeout=1.5) as raw:
            raw.settimeout(3)
            with _tls.wrap_socket(raw, server_hostname=host):
                return time.monotonic() - start
    except OSError:
        return float("inf")


def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    results = _original_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
    addresses = list(dict.fromkeys(r[4][0] for r in results))
    if port != 443 or not isinstance(host, str) or len(addresses) < 2:
        return results
    stamp, timings = _ranked.get(host, (0.0, {}))
    if time.monotonic() - stamp > RANK_SECONDS or set(timings) != set(addresses):
        timings = {ip: _handshake_seconds(host, ip, port) for ip in addresses}
        _ranked[host] = (time.monotonic(), timings)
    return sorted(results, key=lambda r: timings.get(r[4][0], float("inf")))


socket.getaddrinfo = _ipv4_getaddrinfo
