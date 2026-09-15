"""Local inference boundary: loopback or literal private LAN addresses only."""

from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

_LAN = tuple(
    ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def require_local_endpoint(url: str) -> str:
    """Reject cloud hosts, ambiguous DNS names, URL credentials and redirects.

    Use literal LAN addresses (or localhost) so DNS cannot redirect generation
    to a public service. HTTP clients must also disable proxies and redirects.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or port == 0
        ):
            raise ValueError("invalid endpoint")
        if host == "localhost":
            return url.rstrip("/")
        address = ip_address(host)
        if address.is_loopback or any(address in network for network in _LAN):
            return url.rstrip("/")
    except ValueError:
        pass
    raise ValueError("inference requires localhost or a literal private LAN IP address")
