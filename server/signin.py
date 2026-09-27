"""The browser half of a connector's sign-in, shared by every connector.

A connector's router sends the user to the provider and then needs the code the
provider redirects back with. That listener is the same every time, so it lives
here; what differs (authorize URL, scopes, token exchange) stays in the
connector's own folder.

HTTPS, because Slack refuses to register an `http://localhost` redirect URL. The
certificate is self-signed and generated on this machine, so the browser shows a
warning once per install and the user clicks through — that is the cost of a
provider that will not accept loopback HTTP. Providers that accept HTTP get it,
warning-free.

Nothing here is a secret store: the code that comes back is handed to the caller
and never written down. Tokens belong in server/credentials.py.
"""
from __future__ import annotations

import http.server
import secrets as _secrets
import ssl
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config

CERT_DIR = config.SECRETS_DIR / "loopback-tls"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"
# Long enough that a user does not re-accept it constantly, short enough that a
# leaked local key is not useful for years.
CERT_DAYS = 825


def ensure_cert() -> tuple[Path, Path]:
    """A self-signed certificate for localhost, generated once per install.

    Never committed and never shared: a certificate shipped in the repository
    would come with its private key, letting anyone impersonate this listener.
    """
    if CERT_FILE.is_file() and KEY_FILE.is_file():
        return CERT_FILE, KEY_FILE
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=CERT_DAYS))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256()))

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    KEY_FILE.chmod(0o600)
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return CERT_FILE, KEY_FILE


def new_state() -> str:
    """Ties a callback to the sign-in that started it. A callback whose state
    does not match is someone else's redirect, and is refused."""
    return _secrets.token_urlsafe(24)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server's naming
        query = parse_qs(urlparse(self.path).query)
        self.server.captured = {k: v[0] for k, v in query.items()}  # type: ignore[attr-defined]
        done = "code" in query or "error" in query
        if done:
            self.server.done.set()  # type: ignore[attr-defined]
        body = (b"Signed in. You can close this tab."
                if "code" in query else
                b"That did not include an authorization code. Close this tab and retry.")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@contextmanager
def loopback(port: int, path: str = "/callback", https: bool = True):
    """Serve one OAuth redirect on localhost and hand back what it carried.

        with loopback(3000, "/slack/callback") as cb:
            open_browser(authorize_url + f"&redirect_uri={cb.redirect_uri}")
            result = cb.wait()          # {"code": ..., "state": ...}

    The listener stops when the block exits, whether or not a redirect arrived.
    """
    server = http.server.HTTPServer(("localhost", port), _Handler)
    server.captured = {}          # type: ignore[attr-defined]
    server.done = threading.Event()  # type: ignore[attr-defined]
    scheme = "http"
    if https:
        cert, key = ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Callback:
        redirect_uri = f"{scheme}://localhost:{port}{path}"

        @staticmethod
        def wait(timeout: float = 300, state: str | None = None) -> dict:
            if not server.done.wait(timeout):  # type: ignore[attr-defined]
                raise TimeoutError(f"no sign-in completed within {timeout:.0f}s")
            got = dict(server.captured)  # type: ignore[attr-defined]
            if "error" in got:
                raise RuntimeError(f"the provider refused: {got.get('error')}")
            if state is not None and got.get("state") != state:
                raise RuntimeError("callback state did not match; refusing it")
            return got

    try:
        yield Callback
    finally:
        server.shutdown()
        server.server_close()
