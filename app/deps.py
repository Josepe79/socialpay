import hashlib
import secrets
from typing import List

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import security_logger
from app.database import get_db
from app.models import Usuario as UserModel
from app.sessions import get_session as db_get_session


def hash_password(password: str, salt: str = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")
    db_hash = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt_bytes, 100000)
    return f"{salt}:{db_hash.hex()}"


def verify_password(password: str, hashed_password: str) -> bool:
    if not hashed_password or ":" not in hashed_password:
        return False
    salt, _ = hashed_password.split(":", 1)
    return hash_password(password, salt) == hashed_password


async def get_current_user_by_roles(
    request: Request, allowed_roles: List[str], db: Session = Depends(get_db)
):
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None

    sess = db_get_session(db, session_token)
    if not sess or not sess.mfa_verified:
        return None

    user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
    if not user:
        return None

    if user.rol not in allowed_roles:
        anon_id = user.token_anonimo if user.token_anonimo else str(user.id)
        security_logger.warning(
            f"Violacion de acceso detectada. Usuario AnonID: {anon_id}, Rol: '{user.rol}', "
            f"Ruta: '{request.url.path}', Rol requerido: {allowed_roles}"
        )
        raise HTTPException(status_code=403, detail="Acceso denegado. Privilegios insuficientes.")

    return user


async def get_current_admin(request: Request, db: Session = Depends(get_db)):
    return await get_current_user_by_roles(request, ["admin"], db)


async def get_current_upspain(request: Request, db: Session = Depends(get_db)):
    return await get_current_user_by_roles(request, ["upspain"], db)


async def get_current_gestor(request: Request, db: Session = Depends(get_db)):
    return await get_current_user_by_roles(request, ["gestor"], db)


async def get_current_supermercado(request: Request, db: Session = Depends(get_db)):
    return await get_current_user_by_roles(request, ["supermercado"], db)


async def get_current_staff(request: Request, db: Session = Depends(get_db)):
    return await get_current_user_by_roles(
        request, ["admin", "upspain", "gestor", "supermercado"], db
    )
