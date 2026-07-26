import base64

from app import webpush


def test_generate_keys_returns_a_pem_private_key_and_b64url_public_key():
    private_pem, public_b64url = webpush.generate_keys()

    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    # raw uncompressed EC point (0x04 || X || Y) = 65 bytes, base64url
    # without padding - what browsers' PushManager.subscribe expects.
    decoded = base64.urlsafe_b64decode(public_b64url + "=" * (-len(public_b64url) % 4))
    assert len(decoded) == 65
    assert decoded[0] == 0x04


def test_generate_keys_returns_a_different_keypair_each_time():
    private_pem_1, public_1 = webpush.generate_keys()
    private_pem_2, public_2 = webpush.generate_keys()

    assert private_pem_1 != private_pem_2
    assert public_1 != public_2


def test_load_vapid_round_trips_a_generated_private_key():
    private_pem, public_b64url = webpush.generate_keys()

    vapid = webpush.load_vapid(private_pem)

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw_public = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    reloaded_public_b64url = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()
    assert reloaded_public_b64url == public_b64url
