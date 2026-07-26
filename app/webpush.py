import base64

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid


def generate_keys() -> tuple[str, str]:
    """Generates a fresh VAPID keypair for this instance, done once and
    stored globally (see app.db._ensure_vapid_keys_exist) - every user's
    browser subscription is verified against the same instance-wide key
    pair, not a per-user one.

    Returns (private_key_pem, public_key_b64url) - the public key is
    base64url-encoded raw uncompressed EC point bytes, the format browsers'
    `PushManager.subscribe({applicationServerKey: ...})` expects.
    """
    vapid = Vapid()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    raw_public = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    public_b64url = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()
    return private_pem, public_b64url


def load_vapid(private_key_pem: str) -> Vapid:
    """Reconstructs a Vapid object from stored PEM text, ready to pass
    directly as pywebpush.webpush's vapid_private_key argument (it accepts
    a Vapid instance directly, sidestepping its own from_string parsing,
    which expects raw/DER form rather than PEM).
    """
    return Vapid.from_pem(private_key_pem.encode())
