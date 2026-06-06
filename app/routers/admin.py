import secrets
import uuid
from decimal import Decimal
from pathlib import Path

import pyotp
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import BASE_DIR, security_logger
from app.database import SessionLocal, get_db
from app.db_helpers import db_all_products, db_upsert_product
from app.deps import get_current_admin, get_current_user_by_roles, hash_password
from app.models import AsignacionFondosGestor as AFGModel
from app.models import AuditoriaTransaccion as ATModel
from app.models import Producto
from app.models import ProductoSupermercadoGlobal as PSGModel
from app.models import Usuario as UserModel
from app.schemas import BeneficiarySchema, SystemUserCreateSchema, SystemUserUpdateSchema

router = APIRouter()


# ── Catálogo de Productos ──────────────────────────────────────────────────────

@router.get("/api/admin/products",
            summary="Listar Catálogo de Productos Globales",
            tags=["[ADMIN] Administración Global"])
async def list_products(request: Request, supermarket: str = Query(default=None),
                        db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})
    return {"products": db_all_products(db, supermarket)}


@router.post("/api/admin/products",
             summary="Añadir o Actualizar Producto en Catálogo Global",
             tags=["[ADMIN] Administración Global"])
async def add_product(
    request: Request,
    barcode: str = Form(...),
    name: str = Form(...),
    category: str = Form(default="unknown"),
    allowed: bool = Form(default=True),
    supermarket: str = Form(default=None),
    price_ref: float = Form(default=None),
    db: Session = Depends(get_db),
):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})
    db_upsert_product(db, barcode, name, category, allowed, source="manual")

    if supermarket:
        try:
            existing_sg = db.query(PSGModel).filter(
                PSGModel.supermarket == supermarket, PSGModel.barcode == barcode
            ).first()
            if existing_sg:
                existing_sg.price_ref = price_ref
                existing_sg.available = True
            else:
                db.add(PSGModel(supermarket=supermarket, barcode=barcode, price_ref=price_ref))
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "ok", "barcode": barcode, "name": name}


@router.delete("/api/admin/products/{barcode}",
               summary="Eliminar Producto del Catálogo Global",
               tags=["[ADMIN] Administración Global"])
async def delete_product(barcode: str, request: Request, db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})
    try:
        deleted = db.query(Producto).filter(Producto.barcode == barcode).delete()
        db.commit()
        if not deleted:
            return JSONResponse(status_code=404, content={"error": "Producto no encontrado."})
        return {"status": "deleted"}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Logs de Auditoría ─────────────────────────────────────────────────────────

@router.get("/api/admin/audit-logs",
            summary="Consultar Registro de Auditoría Global",
            tags=["[ADMIN] Administración Global"])
async def get_audit_logs(request: Request, db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    logs = db.query(ATModel).order_by(ATModel.timestamp.desc()).all()
    return JSONResponse(content={
        "total_records": len(logs),
        "logs": [
            {
                "transaction_id": str(l.id),
                "user_id": str(l.usuario_uuid) if l.usuario_uuid else None,
                "supermarket": l.supermercado_id,
                "total": float(l.total),
                "timestamp": l.timestamp.isoformat(),
                "status": l.estado,
            }
            for l in logs
        ],
    })


# ── Beneficiarios CRUD ────────────────────────────────────────────────────────

@router.get("/api/admin/beneficiaries",
            summary="Listar Beneficiarios Social Pay",
            tags=["[GESTOR] Acción Social"])
async def list_beneficiaries(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_by_roles(request, ["admin", "gestor"], db)
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    if user.rol == "gestor":
        beneficiaries = db.query(UserModel).filter(
            UserModel.rol == "beneficiario",
            UserModel.gestor_uuid == user.id,
        ).order_by(UserModel.created_at.desc()).all()
    else:
        beneficiaries = db.query(UserModel).filter(
            UserModel.rol == "beneficiario"
        ).order_by(UserModel.created_at.desc()).all()

    return {
        "beneficiaries": [
            {
                "id": str(b.id),
                "token_anonimo": b.token_anonimo,
                "saldo_disponible": float(b.saldo_disponible),
                "gestor_uuid": str(b.gestor_uuid) if b.gestor_uuid else None,
                "codigo_proyecto_fse": b.codigo_proyecto_fse,
            }
            for b in beneficiaries
        ]
    }


@router.post("/api/admin/beneficiaries",
             summary="Crear Beneficiario Social Pay",
             tags=["[GESTOR] Acción Social"])
async def create_beneficiary(item: BeneficiarySchema, request: Request,
                             db: Session = Depends(get_db)):
    current_user = await get_current_user_by_roles(request, ["admin", "gestor"], db)
    if not current_user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    token_val = item.token_anonimo.strip()
    if not token_val:
        return JSONResponse(status_code=400, content={"error": "El token anónimo es obligatorio."})

    existing = db.query(UserModel).filter(UserModel.token_anonimo == token_val).first()
    if existing:
        return JSONResponse(status_code=400, content={"error": "Este token de beneficiario ya existe."})

    gestor_id = None
    codigo_proyecto_fse = item.codigo_proyecto_fse

    if current_user.rol == "gestor":
        gestor_id = current_user.id
        if not codigo_proyecto_fse:
            return JSONResponse(status_code=400,
                                content={"error": "El código de proyecto FSE es obligatorio para el gestor."})
    else:
        if item.gestor_uuid:
            gestor_id = (uuid.UUID(item.gestor_uuid)
                         if isinstance(item.gestor_uuid, str) else item.gestor_uuid)

    tx_session = SessionLocal()
    try:
        with tx_session.begin():
            if gestor_id and codigo_proyecto_fse:
                allocation = tx_session.query(AFGModel).filter(
                    AFGModel.gestor_id == gestor_id,
                    AFGModel.codigo_proyecto_fse == codigo_proyecto_fse,
                ).with_for_update().first()
                if not allocation:
                    raise ValueError("Límite de presupuesto excedido")
                total_existing = tx_session.query(func.sum(UserModel.saldo_disponible)).filter(
                    UserModel.gestor_uuid == gestor_id,
                    UserModel.codigo_proyecto_fse == codigo_proyecto_fse,
                    UserModel.rol == "beneficiario",
                ).scalar() or Decimal("0.00")
                new_total = total_existing + Decimal(str(item.saldo_disponible))
                if new_total > allocation.presupuesto_total:
                    raise ValueError("Límite de presupuesto excedido")
                allocation.presupuesto_consumido = new_total
            tx_session.add(UserModel(
                token_anonimo=token_val,
                saldo_disponible=Decimal(str(item.saldo_disponible)),
                rol="beneficiario",
                gestor_uuid=gestor_id,
                codigo_proyecto_fse=codigo_proyecto_fse,
            ))
    except ValueError as ve:
        return JSONResponse(status_code=400, content={"error": str(ve)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        if "check_presupuesto_consumido_limit" in str(e):
            return JSONResponse(status_code=400, content={"error": "Límite de presupuesto excedido"})
        return JSONResponse(status_code=400, content={"error": f"Error en la base de datos: {str(e)}"})
    finally:
        tx_session.close()

    new_user_db = db.query(UserModel).filter(UserModel.token_anonimo == token_val).first()
    return {
        "status": "created",
        "beneficiary": {
            "id": str(new_user_db.id),
            "token_anonimo": new_user_db.token_anonimo,
            "saldo_disponible": float(new_user_db.saldo_disponible),
            "gestor_uuid": str(new_user_db.gestor_uuid) if new_user_db.gestor_uuid else None,
            "codigo_proyecto_fse": new_user_db.codigo_proyecto_fse,
        },
    }


@router.put("/api/admin/beneficiaries/{user_id}",
            summary="Modificar Beneficiario Social Pay",
            tags=["[GESTOR] Acción Social"])
async def update_beneficiary(user_id: str, item: BeneficiarySchema, request: Request,
                             db: Session = Depends(get_db)):
    current_user = await get_current_user_by_roles(request, ["admin", "gestor"], db)
    if not current_user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    user = db.query(UserModel).filter(
        UserModel.id == user_id, UserModel.rol == "beneficiario"
    ).first()
    if not user:
        return JSONResponse(status_code=404, content={"error": "Beneficiario no encontrado."})

    if current_user.rol == "gestor" and user.gestor_uuid != current_user.id:
        security_logger.warning(
            f"Intento de IDOR detectado. Gestor {current_user.id} intento modificar "
            f"beneficiario {user.id} perteneciente al gestor {user.gestor_uuid}."
        )
        raise HTTPException(status_code=403,
                            detail="Acceso denegado. Este beneficiario no pertenece a tu gestion.")

    token_val = item.token_anonimo.strip()
    if not token_val:
        return JSONResponse(status_code=400, content={"error": "El token anónimo es obligatorio."})

    if db.query(UserModel).filter(UserModel.token_anonimo == token_val,
                                  UserModel.id != user.id).first():
        return JSONResponse(status_code=400, content={"error": "Este token de beneficiario ya está en uso."})

    gestor_id = user.gestor_uuid
    codigo_proyecto_fse = item.codigo_proyecto_fse

    if current_user.rol == "gestor":
        gestor_id = current_user.id
        if not codigo_proyecto_fse:
            return JSONResponse(status_code=400,
                                content={"error": "El código de proyecto FSE es obligatorio para el gestor."})
    else:
        if item.gestor_uuid:
            gestor_id = (uuid.UUID(item.gestor_uuid)
                         if isinstance(item.gestor_uuid, str) else item.gestor_uuid)

    tx_session = SessionLocal()
    try:
        with tx_session.begin():
            if gestor_id and codigo_proyecto_fse:
                allocation = tx_session.query(AFGModel).filter(
                    AFGModel.gestor_id == gestor_id,
                    AFGModel.codigo_proyecto_fse == codigo_proyecto_fse,
                ).with_for_update().first()
                if not allocation:
                    raise ValueError("Límite de presupuesto excedido")
                total_existing = tx_session.query(func.sum(UserModel.saldo_disponible)).filter(
                    UserModel.gestor_uuid == gestor_id,
                    UserModel.codigo_proyecto_fse == codigo_proyecto_fse,
                    UserModel.rol == "beneficiario",
                    UserModel.id != user.id,
                ).scalar() or Decimal("0.00")
                new_total = total_existing + Decimal(str(item.saldo_disponible))
                if new_total > allocation.presupuesto_total:
                    raise ValueError("Límite de presupuesto excedido")
                allocation.presupuesto_consumido = new_total
            tx_user = tx_session.query(UserModel).filter(UserModel.id == user.id).first()
            tx_user.token_anonimo = token_val
            tx_user.saldo_disponible = Decimal(str(item.saldo_disponible))
            tx_user.gestor_uuid = gestor_id
            tx_user.codigo_proyecto_fse = codigo_proyecto_fse
    except ValueError as ve:
        return JSONResponse(status_code=400, content={"error": str(ve)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        if "check_presupuesto_consumido_limit" in str(e):
            return JSONResponse(status_code=400, content={"error": "Límite de presupuesto excedido"})
        return JSONResponse(status_code=400, content={"error": f"Error en la base de datos: {str(e)}"})
    finally:
        tx_session.close()

    db.refresh(user)
    return {
        "status": "updated",
        "beneficiary": {
            "id": str(user.id),
            "token_anonimo": user.token_anonimo,
            "saldo_disponible": float(user.saldo_disponible),
            "gestor_uuid": str(user.gestor_uuid) if user.gestor_uuid else None,
            "codigo_proyecto_fse": user.codigo_proyecto_fse,
        },
    }


@router.delete("/api/admin/beneficiaries/{user_id}",
               summary="Eliminar Beneficiario Social Pay",
               tags=["[GESTOR] Acción Social"])
async def delete_beneficiary(user_id: str, request: Request, db: Session = Depends(get_db)):
    current_user = await get_current_user_by_roles(request, ["admin", "gestor"], db)
    if not current_user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    if current_user.rol == "gestor":
        user = db.query(UserModel).filter(
            UserModel.id == user_id,
            UserModel.rol == "beneficiario",
            UserModel.gestor_uuid == current_user.id,
        ).first()
    else:
        user = db.query(UserModel).filter(
            UserModel.id == user_id, UserModel.rol == "beneficiario"
        ).first()

    if not user:
        return JSONResponse(status_code=404,
                            content={"error": "Beneficiario no encontrado o no pertenece a este gestor."})

    gestor_id = user.gestor_uuid
    codigo_proyecto_fse = user.codigo_proyecto_fse
    saldo_disponible = user.saldo_disponible

    tx_session = SessionLocal()
    try:
        with tx_session.begin():
            if gestor_id and codigo_proyecto_fse:
                allocation = tx_session.query(AFGModel).filter(
                    AFGModel.gestor_id == gestor_id,
                    AFGModel.codigo_proyecto_fse == codigo_proyecto_fse,
                ).with_for_update().first()
                if allocation:
                    allocation.presupuesto_consumido = max(
                        Decimal("0.00"),
                        allocation.presupuesto_consumido - saldo_disponible,
                    )
            tx_user = tx_session.query(UserModel).filter(UserModel.id == user.id).first()
            tx_session.delete(tx_user)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"error": f"Error al eliminar beneficiario: {str(e)}"})
    finally:
        tx_session.close()

    return {"status": "deleted"}


# ── Usuarios del Sistema CRUD ─────────────────────────────────────────────────

@router.get("/api/admin/system-users",
            summary="Listar Usuarios de Personal del Sistema",
            tags=["[ADMIN] Administración Global"])
async def list_system_users(request: Request, db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    users = db.query(UserModel).filter(
        UserModel.rol != "beneficiario"
    ).order_by(UserModel.created_at.desc()).all()
    return {
        "users": [
            {"id": str(u.id), "email": u.email, "rol": u.rol, "mfa_enabled": u.mfa_enabled}
            for u in users
        ]
    }


@router.post("/api/admin/system-users",
             summary="Crear Usuario de Personal del Sistema",
             tags=["[ADMIN] Administración Global"])
async def create_system_user(item: SystemUserCreateSchema, request: Request,
                             db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    email_val = item.email.strip()
    password_val = item.password.strip()
    rol_val = item.rol.strip()

    if not email_val or not password_val or not rol_val:
        return JSONResponse(status_code=400, content={"error": "Todos los campos son obligatorios."})

    if rol_val not in ["admin", "upspain", "gestor", "supermercado"]:
        return JSONResponse(status_code=400, content={"error": "Rol no válido."})

    if db.query(UserModel).filter(UserModel.email == email_val).first():
        return JSONResponse(status_code=400,
                            content={"error": "Este correo electrónico ya está registrado."})

    new_user = UserModel(
        token_anonimo=f"STAFF-TOKEN-{secrets.token_hex(4).upper()}",
        email=email_val,
        hashed_password=hash_password(password_val),
        rol=rol_val,
        mfa_secret=pyotp.random_base32(),
        mfa_enabled=False,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "status": "created",
        "user": {"id": str(new_user.id), "email": new_user.email,
                 "rol": new_user.rol, "mfa_enabled": new_user.mfa_enabled},
    }


@router.put("/api/admin/system-users/{user_id}",
            summary="Modificar Usuario de Personal del Sistema",
            tags=["[ADMIN] Administración Global"])
async def update_system_user(user_id: str, item: SystemUserUpdateSchema, request: Request,
                             db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    user = db.query(UserModel).filter(
        UserModel.id == user_id, UserModel.rol != "beneficiario"
    ).first()
    if not user:
        return JSONResponse(status_code=404, content={"error": "Usuario no encontrado."})

    email_val = item.email.strip()
    rol_val = item.rol.strip()

    if not email_val or not rol_val:
        return JSONResponse(status_code=400, content={"error": "Email y Rol son obligatorios."})
    if rol_val not in ["admin", "upspain", "gestor", "supermercado"]:
        return JSONResponse(status_code=400, content={"error": "Rol no válido."})
    if db.query(UserModel).filter(UserModel.email == email_val, UserModel.id != user.id).first():
        return JSONResponse(status_code=400,
                            content={"error": "Este correo electrónico ya está en uso por otro usuario."})

    user.email = email_val
    user.rol = rol_val
    if item.password and item.password.strip():
        user.hashed_password = hash_password(item.password.strip())
    db.commit()
    db.refresh(user)
    return {
        "status": "updated",
        "user": {"id": str(user.id), "email": user.email,
                 "rol": user.rol, "mfa_enabled": user.mfa_enabled},
    }


@router.delete("/api/admin/system-users/{user_id}",
               summary="Eliminar Usuario de Personal del Sistema",
               tags=["[ADMIN] Administración Global"])
async def delete_system_user(user_id: str, request: Request, db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    if str(admin.id) == user_id:
        return JSONResponse(status_code=400,
                            content={"error": "No puedes eliminar tu propia cuenta de administrador activo."})

    user = db.query(UserModel).filter(
        UserModel.id == user_id, UserModel.rol != "beneficiario"
    ).first()
    if not user:
        return JSONResponse(status_code=404, content={"error": "Usuario no encontrado."})

    db.delete(user)
    db.commit()
    return {"status": "deleted"}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/admin/dashboard", response_class=HTMLResponse,
            summary="Dashboard de Administración Global",
            tags=["[ADMIN] Administración Global"])
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin = await get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
    html_path = BASE_DIR / "templates" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
