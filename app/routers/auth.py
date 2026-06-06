import urllib.parse

import pyotp
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import templates
from app.database import get_db
from app.deps import verify_password
from app.models import Usuario as UserModel
from app.security import generate_csrf_token, limiter, validate_csrf_token
from app.sessions import (
    create_session as db_create_session,
    delete_session as db_delete_session,
    get_session as db_get_session,
    mark_mfa_verified,
)

router = APIRouter()

ROLE_DASHBOARDS = {
    "admin": "/admin/dashboard",
    "upspain": "/upspain/dashboard",
    "gestor": "/gestor/dashboard",
    "supermercado": "/supermercado/dashboard",
}


@router.get("/admin/login", response_class=HTMLResponse,
            summary="Página de Inicio de Sesión de Personal",
            tags=["[ADMIN] Administración Global"])
@limiter.limit("20/minute")
async def login_page(request: Request, error: str = None, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if session_token:
        sess = db_get_session(db, session_token)
        if sess and sess.mfa_verified:
            user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
            if user:
                return RedirectResponse(url=ROLE_DASHBOARDS.get(user.rol, "/admin/dashboard"), status_code=303)
    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "state": "login", "error": error, "csrf_token": csrf_token},
    )
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="strict", secure=True)
    return response


@router.post("/admin/login", summary="Procesar Credenciales de Personal",
             tags=["[ADMIN] Administración Global"])
@limiter.limit("5/minute")
async def process_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not validate_csrf_token(csrf_token, request.cookies.get("csrf_token", "")):
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote("Token de seguridad inválido. Recarga la página."),
            status_code=303,
        )
    try:
        user = db.query(UserModel).filter(UserModel.email == email.strip()).first()
        if not user or user.rol not in ["admin", "upspain", "gestor", "supermercado"]:
            return RedirectResponse(
                url="/admin/login?error=" + urllib.parse.quote("Credenciales incorrectas o acceso no autorizado."),
                status_code=303,
            )
        if not verify_password(password, user.hashed_password):
            return RedirectResponse(
                url="/admin/login?error=" + urllib.parse.quote("Contraseña incorrecta."),
                status_code=303,
            )
        session_token = db_create_session(db, user.id, mfa_verified=False, ttl_minutes=5)
        next_url = "/admin/setup-mfa" if not user.mfa_enabled else "/admin/verify-mfa"
        response = RedirectResponse(url=next_url, status_code=303)
        response.set_cookie(key="session_token", value=session_token, httponly=True, secure=True, samesite="lax")
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303,
        )


@router.get("/admin/setup-mfa", response_class=HTMLResponse,
            summary="Página de Vinculación de MFA",
            tags=["[ADMIN] Administración Global"])
async def setup_mfa_page(request: Request, error: str = None, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if not session_token:
        return RedirectResponse(url="/admin/login", status_code=303)
    sess = db_get_session(db, session_token)
    if not sess:
        return RedirectResponse(url="/admin/login", status_code=303)
    user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    if user.mfa_enabled:
        return RedirectResponse(url="/admin/verify-mfa", status_code=303)

    totp = pyotp.TOTP(user.mfa_secret)
    provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name="SocialPay")
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(provisioning_uri)}"

    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "state": "setup", "qr_url": qr_url,
                 "secret_key": user.mfa_secret, "error": error, "csrf_token": csrf_token},
    )
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="strict", secure=True)
    return response


@router.post("/admin/setup-mfa", summary="Verificar y Activar MFA",
             tags=["[ADMIN] Administración Global"])
@limiter.limit("10/minute")
async def process_setup_mfa(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not validate_csrf_token(csrf_token, request.cookies.get("csrf_token", "")):
        return RedirectResponse(
            url="/admin/setup-mfa?error=" + urllib.parse.quote("Token de seguridad inválido. Recarga la página."),
            status_code=303,
        )
    try:
        session_token = request.cookies.get("session_token")
        if not session_token:
            return RedirectResponse(url="/admin/login", status_code=303)
        sess = db_get_session(db, session_token)
        if not sess:
            return RedirectResponse(url="/admin/login", status_code=303)
        user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
        if not user or user.mfa_enabled:
            return RedirectResponse(url="/admin/login", status_code=303)

        totp = pyotp.TOTP(user.mfa_secret)
        if totp.verify(code.strip()):
            user.mfa_enabled = True
            db.commit()
            mark_mfa_verified(db, session_token, ttl_hours=2)
            return RedirectResponse(url=ROLE_DASHBOARDS.get(user.rol, "/admin/dashboard"), status_code=303)
        return RedirectResponse(
            url="/admin/setup-mfa?error=" + urllib.parse.quote("Código MFA inválido. Reintenta."),
            status_code=303,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303,
        )


@router.get("/admin/verify-mfa", response_class=HTMLResponse,
            summary="Página de Verificación de MFA",
            tags=["[ADMIN] Administración Global"])
async def verify_mfa_page(request: Request, error: str = None, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if not session_token:
        return RedirectResponse(url="/admin/login", status_code=303)
    sess = db_get_session(db, session_token)
    if not sess:
        return RedirectResponse(url="/admin/login", status_code=303)
    if sess.mfa_verified:
        user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
        if user:
            return RedirectResponse(url=ROLE_DASHBOARDS.get(user.rol, "/admin/dashboard"), status_code=303)

    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "state": "verify", "error": error, "csrf_token": csrf_token},
    )
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="strict", secure=True)
    return response


@router.post("/admin/verify-mfa", summary="Procesar Verificación de MFA",
             tags=["[ADMIN] Administración Global"])
@limiter.limit("10/minute")
async def process_verify_mfa(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not validate_csrf_token(csrf_token, request.cookies.get("csrf_token", "")):
        return RedirectResponse(
            url="/admin/verify-mfa?error=" + urllib.parse.quote("Token de seguridad inválido. Recarga la página."),
            status_code=303,
        )
    try:
        session_token = request.cookies.get("session_token")
        if not session_token:
            return RedirectResponse(url="/admin/login", status_code=303)
        sess = db_get_session(db, session_token)
        if not sess:
            return RedirectResponse(url="/admin/login", status_code=303)
        user = db.query(UserModel).filter(UserModel.id == sess.user_id).first()
        if not user:
            return RedirectResponse(url="/admin/login", status_code=303)

        totp = pyotp.TOTP(user.mfa_secret)
        if totp.verify(code.strip()):
            mark_mfa_verified(db, session_token, ttl_hours=2)
            return RedirectResponse(url=ROLE_DASHBOARDS.get(user.rol, "/admin/dashboard"), status_code=303)
        return RedirectResponse(
            url="/admin/verify-mfa?error=" + urllib.parse.quote("Código MFA incorrecto."),
            status_code=303,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303,
        )


@router.get("/admin/logout", summary="Cerrar Sesión de Personal",
            tags=["[ADMIN] Administración Global"])
async def logout(request: Request, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if session_token:
        db_delete_session(db, session_token)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("session_token")
    return response
