import json
import shutil
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import (
    SEED_CATALOG,
    UPLOAD_DIR,
    matcher,
    normalize,
    ocr_ticket_via_gemini,
    templates,
)
from app.database import get_db
from app.db_helpers import (
    db_get_barcode_by_mapping,
    db_get_by_barcode,
    db_save_ticket_mapping,
    db_search,
    db_upsert_product,
)
from app.models import AuditoriaTransaccion as ATModel
from app.models import ProductoSupermercado as PSModel
from app.models import Usuario as UserModel
from app.security import generate_csrf_token, limiter, validate_csrf_token

router = APIRouter()


@router.get("/", response_class=HTMLResponse,
            summary="Acceso del Beneficiario / Index",
            tags=["[BENEFICIARIO] Aplicación Móvil"])
async def read_root(request: Request, token: str = Query(None), db: Session = Depends(get_db)):
    token_val = token or request.cookies.get("beneficiary_token")
    user = None
    if token_val:
        user = db.query(UserModel).filter(UserModel.token_anonimo == token_val.strip()).first()

    if not user:
        csrf_token = generate_csrf_token()
        resp = templates.TemplateResponse(
            request=request,
            name="beneficiary_login.html",
            context={"request": request,
                     "error": "Token no válido o no proporcionado" if token_val else None,
                     "csrf_token": csrf_token},
        )
        resp.set_cookie("csrf_token", csrf_token, httponly=False, samesite="strict", secure=True)
        return resp

    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "user": user},
    )
    response.set_cookie(key="beneficiary_token", value=user.token_anonimo, httponly=True)
    return response


@router.post("/beneficiario/login",
             summary="Iniciar Sesión del Beneficiario",
             tags=["[BENEFICIARIO] Aplicación Móvil"])
@limiter.limit("10/minute")
async def process_beneficiary_login(
    request: Request,
    token: str = Form(...),
    csrf_token: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not validate_csrf_token(csrf_token, request.cookies.get("csrf_token", "")):
        csrf_new = generate_csrf_token()
        resp = templates.TemplateResponse(
            request=request,
            name="beneficiary_login.html",
            context={"request": request,
                     "error": "Token de seguridad inválido. Recarga la página.",
                     "csrf_token": csrf_new},
        )
        resp.set_cookie("csrf_token", csrf_new, httponly=False, samesite="strict", secure=True)
        return resp

    token_val = token.strip()
    user = db.query(UserModel).filter(UserModel.token_anonimo == token_val).first()
    if not user:
        csrf_new = generate_csrf_token()
        resp = templates.TemplateResponse(
            request=request,
            name="beneficiary_login.html",
            context={"request": request,
                     "error": "Código de acceso no válido.",
                     "entered_token": token_val,
                     "csrf_token": csrf_new},
        )
        resp.set_cookie("csrf_token", csrf_new, httponly=False, samesite="strict", secure=True)
        return resp

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="beneficiary_token", value=user.token_anonimo, httponly=True)
    return response


@router.get("/beneficiario/logout",
            summary="Cerrar Sesión del Beneficiario",
            tags=["[BENEFICIARIO] Aplicación Móvil"])
async def beneficiary_logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("beneficiary_token")
    return response


@router.post("/scan-product",
             summary="Escaneo de Código de Barras",
             tags=["[BENEFICIARIO] Aplicación Móvil"])
async def scan_product(request: Request, barcode: str = Form(...), db: Session = Depends(get_db)):
    beneficiary_token = request.cookies.get("beneficiary_token")
    if not beneficiary_token:
        return JSONResponse(status_code=403, content={"error": "Acceso denegado. Beneficiario no autenticado."})
    user = db.query(UserModel).filter(UserModel.token_anonimo == beneficiary_token.strip()).first()
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso denegado. Token inválido."})

    product = db_get_by_barcode(db, barcode)
    if product:
        return {"name": product["name"], "allowed": product["allowed"]}

    info = matcher.get_product_info(barcode)
    if "Error" not in info.get("name", "Error") and "desconocido" not in info.get("name", ""):
        db_upsert_product(db, barcode=barcode, name=info["name"],
                          category="unknown", allowed=info["allowed"], source="off")
    return info


@router.post("/scan/manual",
             summary="Adición Manual al Carrito",
             tags=["[BENEFICIARIO] Aplicación Móvil"])
async def scan_manual(product_name: str = Form(...), price: float = Form(...)):
    return {"status": "success", "name": product_name, "price": price}


@router.get("/api/search",
            summary="Buscador Predictivo de Productos",
            tags=["[BENEFICIARIO] Aplicación Móvil"])
async def search_products(q: str, supermarket: str = Query(default=None),
                          db: Session = Depends(get_db)):
    if not q or len(q.strip()) < 2:
        return {"products": []}

    db_results = db_search(db, q, supermarket)

    try:
        resp = requests.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params={"search_terms": q, "search_simple": "1",
                    "action": "process", "json": "1", "page_size": "5"},
            headers={"User-Agent": "SocialPayMVP/1.0"},
            timeout=2,
        )
        if resp.status_code == 200:
            existing_names = {normalize(r["name"]) for r in db_results}
            for p in resp.json().get("products", []):
                name = (p.get("product_name_es") or p.get("product_name") or "").strip()
                if name and normalize(name) not in existing_names:
                    db_results.append({"name": name, "category": "unknown", "allowed": True})
                    existing_names.add(normalize(name))
    except Exception:
        pass

    return {"products": db_results, "source": "db"}


@router.post(
    "/upload-ticket",
    summary="Subir Ticket y Validar mediante OCR",
    tags=["[BENEFICIARIO] Aplicación Móvil"],
    responses={
        200: {"description": "Validación completada con éxito."},
        400: {"description": "Saldo insuficiente."},
        403: {"description": "Beneficiario no autenticado."},
    },
)
@limiter.limit("3/minute")
async def upload_ticket(
    request: Request,
    ticket: UploadFile = File(...),
    cart_total: float = Form(...),
    cart_items: str = Form(...),
    supermarket: str = Form(...),
    db: Session = Depends(get_db),
):
    from logic.validator import TicketValidator

    beneficiary_token = request.cookies.get("beneficiary_token")
    user = None
    if beneficiary_token:
        user = db.query(UserModel).filter(UserModel.token_anonimo == beneficiary_token.strip()).first()

    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso denegado. Beneficiario no autenticado."})

    try:
        parsed_cart_items = json.loads(cart_items)
    except json.JSONDecodeError:
        parsed_cart_items = []

    recalculated_total = 0.0
    for item in parsed_cart_items:
        barcode = item.get("barcode")
        db_price = None
        if barcode:
            local_prod = db.query(PSModel).filter(
                PSModel.supermercado_id == supermarket,
                PSModel.codigo_barras == barcode,
            ).first()
            if local_prod:
                db_price = float(local_prod.precio)
            else:
                global_prod = db_get_by_barcode(db, barcode)
                if global_prod and global_prod.get("price_ref") is not None:
                    db_price = float(global_prod["price_ref"])
                else:
                    for b, _n, _c in SEED_CATALOG:
                        if b == barcode:
                            db_price = 1.00
                            break
        if db_price is not None:
            item["price"] = db_price
        else:
            db_price = float(item.get("price", 0.0))
        recalculated_total += db_price

    cart_total = recalculated_total

    if float(user.saldo_disponible) < cart_total:
        return JSONResponse(
            status_code=400,
            content={"error": f"Saldo insuficiente. Saldo disponible: €{float(user.saldo_disponible):.2f}"},
        )

    file_ext = Path(ticket.filename).suffix.lower() if ticket.filename else ".jpg"
    safe_ext = file_ext if file_ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
    file_path = UPLOAD_DIR / f"{uuid.uuid4()}{safe_ext}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(ticket.file, buffer)

    ticket_data, debug_error = ocr_ticket_via_gemini(file_path, parsed_cart_items, supermarket)

    using_fallback = False
    if not ticket_data:
        using_fallback = True
        print(f"[OCR] Falling back to simulation. Reason: {debug_error}")
        ticket_data = {
            "total": cart_total,
            "items": [{"name": item["name"], "price": item["price"]} for item in parsed_cart_items],
        }

    validator = TicketValidator()
    report = validator.validate(
        cart_items=parsed_cart_items,
        cart_total=cart_total,
        ticket_data=ticket_data,
        get_mapping_func=lambda sm, name: db_get_barcode_by_mapping(db, sm, name),
        supermarket=supermarket,
    )
    report["using_fallback"] = using_fallback
    report["debug_error"] = debug_error

    if report["status"] == "validated":
        for mapping in report.get("learned_mappings", []):
            db_save_ticket_mapping(
                db,
                supermarket=mapping["supermarket"],
                raw_ticket_name=mapping["raw_name"],
                barcode=mapping["barcode"],
            )

        from decimal import Decimal

        user.saldo_disponible -= Decimal(str(cart_total))
        new_audit = ATModel(
            usuario_uuid=user.id,
            supermercado_id=supermarket,
            total=Decimal(str(cart_total)),
            estado="APPROVED",
        )
        db.add(new_audit)
        db.commit()
        db.refresh(new_audit)

    return report
