from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.deps import get_current_gestor
from app.models import AsignacionFondosGestor as AFGModel
from app.models import AuditoriaTransaccion as ATModel
from app.models import Usuario as UserModel

router = APIRouter()


@router.get(
    "/api/gestor/dashboard-data",
    summary="Obtener Datos de Gestión de Acción Social",
    tags=["[GESTOR] Acción Social"],
)
async def get_gestor_dashboard_data(request: Request, db: Session = Depends(get_db)):
    user = await get_current_gestor(request, db)
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    gestor_id = user.id

    beneficiaries = db.query(UserModel).filter(
        UserModel.rol == "beneficiario",
        UserModel.gestor_uuid == gestor_id,
    ).order_by(UserModel.created_at.desc()).all()

    allocations = db.query(AFGModel).filter(
        AFGModel.gestor_id == gestor_id
    ).order_by(AFGModel.created_at.desc()).all()

    beneficiary_ids = [b.id for b in beneficiaries]
    sales = []
    if beneficiary_ids:
        sales = db.query(ATModel).filter(
            ATModel.usuario_uuid.in_(beneficiary_ids)
        ).order_by(ATModel.timestamp.desc()).all()

    return {
        "gestor_id": str(gestor_id),
        "email": user.email,
        "beneficiaries": [
            {
                "id": str(b.id),
                "token_anonimo": b.token_anonimo,
                "saldo_disponible": float(b.saldo_disponible),
                "codigo_proyecto_fse": b.codigo_proyecto_fse,
            }
            for b in beneficiaries
        ],
        "allocations": [
            {
                "id": str(a.id),
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
    }


@router.get("/gestor/dashboard", response_class=HTMLResponse,
            summary="Dashboard de Acción Social (Gestor)",
            tags=["[GESTOR] Acción Social"])
async def gestor_dashboard(request: Request, db: Session = Depends(get_db)):
    user = await get_current_gestor(request, db)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    html_path = BASE_DIR / "templates" / "gestor_dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
