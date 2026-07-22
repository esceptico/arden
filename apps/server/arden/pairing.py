"""Phone pairing: build the deep link + render a terminal QR code.

QR rendering uses segno (pure-Python, zero-dependency). The pairing payload
is a deep link the mobile client scans to capture {serverURL, apiKey}.
"""

import io
import socket
from urllib.parse import quote

import segno

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def resolve_lan_host(host: str) -> str:
    """Return a LAN-reachable host for the QR/deep-link.

    A bind host of localhost/0.0.0.0 is not reachable from a phone, so we
    discover the primary outbound interface IP. If discovery fails we fall
    back to loopback.
    """
    if host not in _LOOPBACK_HOSTS:
        return host
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are sent; this just selects the default-route interface.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def build_pairing_link(host: str, port: int, api_key: str) -> str:
    url = f"http://{host}:{port}"
    return f"arden://connect?url={quote(url, safe='')}&key={quote(api_key, safe='')}"


def render_qr(link: str) -> str:
    qr = segno.make(link, error="m")
    buf = io.StringIO()
    qr.terminal(out=buf, compact=True)
    return buf.getvalue().rstrip("\n")


def build_pairing(host: str, port: int, api_key: str) -> tuple[str, str, str]:
    """Return (lan_host, deep_link, terminal_qr) for a LAN-reachable pairing."""
    lan_host = resolve_lan_host(host)
    link = build_pairing_link(lan_host, port, api_key)
    return lan_host, link, render_qr(link)
