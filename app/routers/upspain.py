import re
import secrets
import uuid
from datetime import datetime
from decimal import Decimal

import pyotp
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import BASE_DIR, parse_spanish_float, security_logger
from app.database import SessionLocal, get_db
from app.deps import get_current_upspain, hash_password
from app.models import AsignacionFondosGestor as AFGModel
from app.models import AuditoriaTransaccion as ATModel
from app.models import Usuario as UserModel
from app.schemas import AsignarFondosSchema, GestorCreateSchema

router = APIRouter()


@router.post(
    "/api/upspain/asignar-fondos",
    summary="Asignar Presupuesto FSE+ a Gestor",
    tags=["[UP SPAIN] Supervisor Financiero"],
)
async def asignar_fondos(item: AsignarFondosSchema, request: Request,
                         db: Session = Depends(get_db)):
    current_user = await get_current_upspain(request, db)
    if not current_user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    gestor_uuid = uuid.UUID(item.gestor_id) if isinstance(item.gestor_id, str) else item.gestor_id
    gestor = db.query(UserModel).filter(
        UserModel.id == gestor_uuid, UserModel.rol == "gestor"
    ).first()
    if not gestor:
        return JSONResponse(status_code=400,
                            content={"error": "El gestor especificado no existe o no tiene el rol de gestor."})

    try:
        presupuesto_total_val = parse_spanish_float(item.presupuesto_total)
        tasa_val = parse_spanish_float(item.tasa_cofinanciacion)
    except Exception:
        return JSONResponse(status_code=400,
                            content={"error": "El presupuesto y la tasa deben ser números válidos."})

    if presupuesto_total_val <= 0:
        return JSONResponse(status_code=400, content={"error": "El presupuesto total debe ser positivo."})
    if not (0 <= tasa_val <= 1):
        return JSONResponse(status_code=400,
                            content={"error": "La tasa debe estar entre 0.00 y 1.00."})

    existing = db.query(AFGModel).filter(
        AFGModel.gestor_id == gestor_uuid,
        AFGModel.codigo_proyecto_fse == item.codigo_proyecto_fse,
    ).first()

    if existing:
        existing.presupuesto_total = Decimal(str(presupuesto_total_val))
        existing.tasa_cofinanciacion = Decimal(str(tasa_val))
        db.commit()
        db.refresh(existing)
        return {
            "status": "updated",
            "allocation": {
                "id": str(existing.id),
                "gestor_id": str(existing.gestor_id),
                "codigo_proyecto_fse": existing.codigo_proyecto_fse,
                "presupuesto_total": float(existing.presupuesto_total),
                "presupuesto_consumido": float(existing.presupuesto_consumido),
                "tasa_cofinanciacion": float(existing.tasa_cofinanciacion),
            },
        }
    else:
        new_alloc = AFGModel(
            gestor_id=gestor_uuid,
            codigo_proyecto_fse=item.codigo_proyecto_fse,
            presupuesto_total=Decimal(str(presupuesto_total_val)),
            tasa_cofinanciacion=Decimal(str(tasa_val)),
            presupuesto_consumido=Decimal("0.00"),
        )
        db.add(new_alloc)
        db.commit()
        db.refresh(new_alloc)
        return {
            "status": "created",
            "allocation": {
                "id": str(new_alloc.id),
                "gestor_id": str(new_alloc.gestor_id),
                "codigo_proyecto_fse": new_alloc.codigo_proyecto_fse,
                "presupuesto_total": float(new_alloc.presupuesto_total),
                "presupuesto_consumido": float(new_alloc.presupuesto_consumido),
                "tasa_cofinanciacion": float(new_alloc.tasa_cofinanciacion),
            },
        }


@router.post(
    "/api/upspain/crear-gestor",
    summary="Crear Perfil de Gestor Social",
    tags=["[UP SPAIN] Supervisor Financiero"],
)
async def crear_gestor(item: GestorCreateSchema, request: Request,
                       db: Session = Depends(get_db)):
    current_user = await get_current_upspain(request, db)
    if not current_user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    nombre_val = item.nombre_institucion.strip()
    cif_val = re.sub(r"[\s\-]", "", item.cif).upper()
    direccion_val = item.direccion.strip()
    codigo_proyecto_val = item.codigo_proyecto_fse.strip()
    try:
        presupuesto_val = parse_spanish_float(item.presupuesto_inicial)
        tasa_val = parse_spanish_float(item.tasa_cofinanciacion)
    except Exception:
        return JSONResponse(status_code=400,
                            content={"error": "El presupuesto inicial y la tasa deben ser números válidos."})
    responsable_val = item.responsable.strip()
    email_val = item.email.strip().lower()
    password_val = item.password.strip()
    movil_val = item.movil_mfa.strip()

    if not all([nombre_val, cif_val, direccion_val, codigo_proyecto_val,
                responsable_val, email_val, password_val, movil_val]):
        return JSONResponse(status_code=400,
                            content={"error": "Todos los campos del formulario son obligatorios."})

    if not re.match(r"^[A-HJNP-SU-W]\d{7}[0-9A-J]$", cif_val):
        return JSONResponse(status_code=400,
                            content={"error": "El CIF no cumple el formato español (ej: A1234567B)."})
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email_val):
        return JSONResponse(status_code=400,
                            content={"error": "El email institucional no tiene formato válido."})
    if presupuesto_val <= 0:
        return JSONResponse(status_code=400,
                            content={"error": "El presupuesto inicial debe ser positivo."})
    if not (0 <= tasa_val <= 1):
        return JSONResponse(status_code=400,
                            content={"error": "La tasa debe estar entre 0.00 y 1.00."})

    if db.query(UserModel).filter(UserModel.email == email_val).first():
        return JSONResponse(status_code=400,
                            content={"error": "Este correo electrónico institucional ya está registrado."})

    tx_session = SessionLocal()
    new_gestor_id = None
    created_at_time = datetime.utcnow()
    try:
        with tx_session.begin():
            new_gestor = UserModel(
                token_anonimo=f"GESTOR-TOKEN-{secrets.token_hex(4).upper()}",
                email=email_val,
                hashed_password=hash_password(password_val),
                rol="gestor",
                mfa_secret=pyotp.random_base32(),
                mfa_enabled=False,
                creado_por=current_user.id,
                nombre_institucion=nombre_val,
                cif=cif_val,
                direccion=direccion_val,
                responsable=responsable_val,
                movil_mfa=movil_val,
                created_at=created_at_time,
            )
            tx_session.add(new_gestor)
            tx_session.flush()
            new_gestor_id = new_gestor.id
            tx_session.add(AFGModel(
                gestor_id=new_gestor.id,
                codigo_proyecto_fse=codigo_proyecto_val,
                presupuesto_total=Decimal(str(presupuesto_val)),
                presupuesto_consumido=Decimal("0.00"),
                tasa_cofinanciacion=Decimal(str(tasa_val)),
                created_at=created_at_time,
            ))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=400,
                            content={"error": f"Error al persistir el perfil del gestor: {str(e)}"})
    finally:
        tx_session.close()

    security_logger.warning(
        f"[COMPLIANCE FSE+] Gestor creado. Creador (Up Spain): {current_user.id}, "
        f"Gestor: {new_gestor_id}, CIF: {cif_val}, Proyecto: {codigo_proyecto_val}, "
        f"Presupuesto: €{presupuesto_val}"
    )

    return {
        "status": "created",
        "gestor": {
            "id": str(new_gestor_id),
            "email": email_val,
            "nombre_institucion": nombre_val,
            "cif": cif_val,
            "codigo_proyecto_fse": codigo_proyecto_val,
            "presupuesto_inicial": presupuesto_val,
            "creado_por": str(current_user.id),
            "created_at": created_at_time.isoformat(),
        },
    }


@router.get(
    "/api/upspain/dashboard-data",
    summary="Obtener Datos del Supervisor Financiero",
    tags=["[UP SPAIN] Supervisor Financiero"],
)
async def get_upspain_dashboard_data(request: Request, db: Session = Depends(get_db)):
    user = await get_current_upspain(request, db)
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    gestores = db.query(UserModel).filter(UserModel.rol == "gestor").all()
    allocations = db.query(AFGModel).order_by(AFGModel.created_at.desc()).all()
    sales = db.query(ATModel).order_by(ATModel.timestamp.desc()).all()

    total_budget = sum(float(a.presupuesto_total) for a in allocations)
    total_consumed = sum(float(a.presupuesto_consumido) for a in allocations)
    gestor_map = {str(g.id): g.email for g in gestores}

    return {
        "gestores": [{"id": str(g.id), "email": g.email} for g in gestores],
        "allocations": [
            {
                "id": str(a.id),
                "gestor_id": str(a.gestor_id),
                "gestor_email": gestor_map.get(str(a.gestor_id), "Desconocido"),
                "codigo_proyecto_fse": a.codigo_proyecto_fse,
                "presupuesto_total": float(a.presupuesto_total),
                "presupuesto_consumido": float(a.presupuesto_consumido),
                "tasa_cofinanciacion": float(a.tasa_cofinanciacion),
            }
            for a in allocations
        ],
        "sales": [
            {
                "id": str(s.id),
                "usuario_uuid": str(s.usuario_uuid) if s.usuario_uuid else None,
                "supermercado_id": s.supermercado_id,
                "total": float(s.total),
                "timestamp": s.timestamp.isoformat(),
                "estado": s.estado,
            }
            for s in sales
        ],
        "kpis": {
            "total_budget": total_budget,
            "total_consumed": total_consumed,
            "net_active_projects": len(set(a.codigo_proyecto_fse for a in allocations)),
        },
    }


@router.get("/upspain/dashboard", response_class=HTMLResponse,
            summary="Dashboard de Supervisor Financiero (Up Spain)",
            tags=["[UP SPAIN] Supervisor Financiero"])
async def upspain_dashboard(request: Request, db: Session = Depends(get_db)):
    user = await get_current_upspain(request, db)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    html_path = BASE_DIR / "templates" / "upspain_dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
