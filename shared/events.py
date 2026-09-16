import json
from pathlib import Path

from shared.crypto import sign_event, verify_signature


def build_event(data: dict, origin: str, private_key_path: Path) -> dict:
    payload = {"origin": origin, **data}
    signature = sign_event(payload, private_key_path)
    return {"data": payload, "Signature": signature}


def parse_event(body: bytes) -> dict | None:
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def validate_event(envelope: dict, keys_dir: Path) -> dict | None:
    if not envelope or "data" not in envelope or "Signature" not in envelope:
        return None

    data = envelope["data"]
    origin = data.get("origin")
    if not origin:
        return None

    public_key_path = keys_dir / f"{origin}_public.pem"
    if not public_key_path.exists():
        print(f" [x] Chave publica nao encontrada para origem: {origin}")
        return None

    if not verify_signature(data, envelope["Signature"], public_key_path):
        print(f" [x] Assinatura invalida de origem: {origin}")
        return None

    return data


def serialize_event(envelope: dict) -> bytes:
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")
