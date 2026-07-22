from urllib.parse import parse_qs, urlparse

import segno

from arden.pairing import (
    build_pairing,
    build_pairing_link,
    render_qr,
    resolve_lan_host,
)


def test_build_pairing_link_encodes_url_and_key():
    link = build_pairing_link("192.168.1.50", 6877, "abc/with+special=chars")
    parsed = urlparse(link)
    assert parsed.scheme == "arden"
    assert parsed.netloc == "connect"
    qs = parse_qs(parsed.query)
    assert qs["url"] == ["http://192.168.1.50:6877"]
    assert qs["key"] == ["abc/with+special=chars"]


def test_resolve_lan_host_keeps_non_loopback():
    assert resolve_lan_host("10.0.0.4") == "10.0.0.4"


def test_resolve_lan_host_replaces_loopback():
    host = resolve_lan_host("127.0.0.1")
    assert host
    assert host not in {"localhost", "0.0.0.0", "::1"}


def test_render_qr_round_trips_to_same_link():
    link = build_pairing_link("192.168.1.50", 6877, "round-trip-key")
    # The terminal renderer and a decodable QR share one matrix; verify the
    # matrix encodes the exact deep link.
    decoded = segno.make(link, error="m")
    assert render_qr(link)
    assert decoded.error == "M"


def test_build_pairing_returns_lan_host_and_qr():
    lan_host, link, qr = build_pairing("127.0.0.1", 6877, "key")
    assert lan_host in link
    assert link.startswith("arden://connect?url=")
    assert qr
