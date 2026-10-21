"""Ed25519-Signaturen für Pakete und Repository-Indizes."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


class SignatureError(Exception):
    pass


def key_id(raw_pub: bytes) -> str:
    return hashlib.sha256(raw_pub).hexdigest()[:16]


@dataclass
class PublicKey:
    id: str
    key: Ed25519PublicKey
    comment: str = ""

    @classmethod
    def load(cls, path: Path) -> "PublicKey":
        fields = _read_kv(path, "syp-public-key v1")
        raw = base64.b64decode(fields["key"])
        k = cls(key_id(raw), Ed25519PublicKey.from_public_bytes(raw), fields.get("comment", ""))
        if fields.get("id") and fields["id"] != k.id:
            raise SignatureError(f"Schlüssel-ID passt nicht zum Schlüssel: {path}")
        return k


@dataclass
class SecretKey:
    key: Ed25519PrivateKey
    comment: str = ""

    @property
    def raw_pub(self) -> bytes:
        return self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @property
    def id(self) -> str:
        return key_id(self.raw_pub)

    @classmethod
    def generate(cls, comment: str = "") -> "SecretKey":
        return cls(Ed25519PrivateKey.generate(), comment)

    @classmethod
    def load(cls, path: Path) -> "SecretKey":
        fields = _read_kv(path, "syp-secret-key v1")
        seed = base64.b64decode(fields["seed"])
        return cls(Ed25519PrivateKey.from_private_bytes(seed), fields.get("comment", ""))

    def save(self, secret_path: Path, public_path: Path) -> None:
        seed = self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.touch(mode=0o600)
        secret_path.write_text(
            f"syp-secret-key v1\nid: {self.id}\nseed: {base64.b64encode(seed).decode()}\n"
            f"comment: {self.comment}\n"
        )
        public_path.write_text(
            f"syp-public-key v1\nid: {self.id}\nkey: {base64.b64encode(self.raw_pub).decode()}\n"
            f"comment: {self.comment}\n"
        )

    def sign(self, data: bytes) -> dict:
        return {"key_id": self.id, "alg": "ed25519",
                "sig": base64.b64encode(self.key.sign(data)).decode()}


class Keyring:
    def __init__(self, keys: list[PublicKey] | None = None):
        self.keys = {k.id: k for k in keys or []}

    @classmethod
    def from_dir(cls, d: Path) -> "Keyring":
        return cls([PublicKey.load(p) for p in sorted(d.glob("*.pub"))] if d.is_dir() else [])

    def verify(self, data: bytes, sig: dict) -> PublicKey:
        k = self.keys.get(sig.get("key_id", ""))
        if k is None:
            raise SignatureError(f"Unbekannter Signaturschlüssel {sig.get('key_id')!r}")
        try:
            k.key.verify(base64.b64decode(sig["sig"]), data)
        except (InvalidSignature, KeyError, ValueError) as e:
            raise SignatureError("Signatur ungültig") from e
        return k


def _read_kv(path: Path, magic: str) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != magic:
        raise SignatureError(f"{path}: kein gültiges Schlüsselformat ({magic})")
    out = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def sign_file(secret: SecretKey, path: Path) -> Path:
    sig = path.with_name(path.name + ".sig")
    sig.write_text(json.dumps(secret.sign(path.read_bytes())))
    return sig


def verify_file(keyring: Keyring, path: Path, sig_path: Path | None = None) -> PublicKey:
    sig_path = sig_path or path.with_name(path.name + ".sig")
    if not sig_path.exists():
        raise SignatureError(f"Signatur fehlt: {sig_path}")
    return keyring.verify(path.read_bytes(), json.loads(sig_path.read_text()))
