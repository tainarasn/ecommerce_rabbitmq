import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def canonical_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def hash_content(data: dict) -> bytes:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).digest()


def load_private_key(path: Path):
    with open(path, "rb") as key_file:
        return serialization.load_pem_private_key(key_file.read(), password=None)


def load_public_key(path: Path):
    with open(path, "rb") as key_file:
        return serialization.load_pem_public_key(key_file.read())


def sign_event(data: dict, private_key_path: Path) -> str:
    private_key = load_private_key(private_key_path)
    content = canonical_json(data).encode("utf-8")
    signature = private_key.sign(content, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")


def verify_signature(data: dict, signature_b64: str, public_key_path: Path) -> bool:
    try:
        public_key = load_public_key(public_key_path)
        signature = base64.b64decode(signature_b64.encode("utf-8"))
        content = canonical_json(data).encode("utf-8")
        public_key.verify(signature, content, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def generate_key_pair(private_key_path: Path, public_key_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.parent.mkdir(parents=True, exist_ok=True)

    with open(private_key_path, "wb") as private_file:
        private_file.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    with open(public_key_path, "wb") as public_file:
        public_file.write(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
