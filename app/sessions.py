import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session as DBSession
from app.models import AdminSession


def create_session(db: DBSession, user_id, mfa_verified: bool = False, ttl_minutes: int = 5) -> str:
    token = secrets.token_hex(32)
    sess = AdminSession(
        token=token,
        user_id=user_id,
        mfa_verified=mfa_verified,
        expires=datetime.utcnow() + timedelta(minutes=ttl_minutes),
    )
    db.add(sess)
    db.commit()
    return token


def get_session(db: DBSession, token: str):
    if not token:
        return None
    sess = db.query(AdminSession).filter(AdminSession.token == token).first()
    if not sess:
        return None
    if datetime.utcnow() > sess.expires:
        db.delete(sess)
        db.commit()
        return None
    return sess


def mark_mfa_verified(db: DBSession, token: str, ttl_hours: int = 2):
    sess = db.query(AdminSession).filter(AdminSession.token == token).first()
    if sess:
        sess.mfa_verified = True
        sess.expires = datetime.utcnow() + timedelta(hours=ttl_hours)
        db.commit()


def delete_session(db: DBSession, token: str):
    db.query(AdminSession).filter(AdminSession.token == token).delete()
    db.commit()


def purge_expired(db: DBSession):
    db.query(AdminSession).filter(AdminSession.expires < datetime.utcnow()).delete()
    db.commit()
