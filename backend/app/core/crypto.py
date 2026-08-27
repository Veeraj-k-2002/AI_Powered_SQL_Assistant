"""Encryption helpers for secrets persisted by the application."""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def encrypt_secret(value: str) -> str:
    return Fernet(settings.fernet_key()).encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return Fernet(settings.fernet_key()).decrypt(value.encode()).decode()
    except InvalidToken as exc:
        # Do not expose encrypted values or details of the cryptographic failure.
        raise ValueError("Stored database credentials cannot be decrypted") from exc
