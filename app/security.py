import os
import secrets
from itsdangerous import URLSafeTimedSerializer
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

_SECRET = os.environ.get("SESSION_SECRET_KEY", "dev-secret-change-in-production")
_serializer = URLSafeTimedSerializer(_SECRET)


def generate_csrf_token() -> str:
    """Genera un token CSRF aleatorio (patrón double-submit cookie)."""
    return secrets.token_hex(16)


def validate_csrf_token(form_token: str, cookie_token: str) -> bool:
    """Valida que el token del formulario coincide con la cookie CSRF."""
    if not form_token or not cookie_token:
        return False
    return secrets.compare_digest(form_token, cookie_token)
