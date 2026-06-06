import csv
import io
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import BASE_DIR, SEED_CATALOG, security_logger
from app.database import get_db
from app.deps import get_current_supermercado
from app.models import AuditoriaTransaccion as ATModel
from app.models import ProductoSupermercado as PSModel
from app.models import Usuario as UserModel
from app.schemas import SupermarketProductSchema

router = APIRouter()


@router.post(
    "/api/supermercado/producto",
    summary="Añadir o Actualizar Referencia en Supermercado",
    tags=["[SUPERMERCADO] Catálogos"],
)
async def add_or_update_supermarket_product(
    item: SupermarketProductSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_supermercado),
):
    user_supermarket_id = current_user.email.split("@")[0]
    if item.supermercado_id != user_supermarket_id:
        security_logger.warning(
            f"Intento de IDOR detectado. Supermercado '{user_supermarket_id}' "
            f"intento modificar catalogo de '{item.supermercado_id}'"
        )
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado. No autorizado a modificar este supermercado.",
        )

    if not item.codigo_barras or not item.codigo_barras.strip():
        return JSONResponse(status_code=400, content={"error": "El código de barras es obligatorio."})
    if not item.nombre or not item.nombre.strip():
        return JSONResponse(status_code=400, content={"error": "El nombre es obligatorio."})
    if item.precio <= 0:
        return JSONResponse(status_code=400, content={"error": "El precio debe ser mayor que 0."})

    existing = db.query(PSModel).filter(
        PSModel.supermercado_id == item.supermercado_id,
        PSModel.codigo_barras == item.codigo_barras,
    ).first()

    if existing:
        existing.nombre = item.nombre
        existing.precio = item.precio
        existing.categoria_fse = item.categoria_fse
        existing.palabras_clave_ocr = item.palabras_clave_ocr
        db.commit()
        db.refresh(existing)
        action, prod_id = "updated", existing.id
    else:
        new_prod = PSModel(
            supermercado_id=item.supermercado_id,
            codigo_barras=item.codigo_barras,
            nombre=item.nombre,
            precio=item.precio,
            categoria_fse=item.categoria_fse,
            palabras_clave_ocr=item.palabras_clave_ocr,
        )
        db.add(new_prod)
        db.commit()
        db.refresh(new_prod)
        action, prod_id = "created", new_prod.id

    return {
        "status": action,
        "id": prod_id,
        "supermercado_id": item.supermercado_id,
        "codigo_barras": item.codigo_barras,
        "nombre": item.nombre,
    }


@router.post(
    "/api/supermercado/upload-batch",
    summary="Carga Masiva de Productos de Supermercado (CSV)",
    tags=["[SUPERMERCADO] Catálogos"],
)
async def upload_batch_products(
    request: Request,
    supermercado_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_supermercado),
):
    user_supermarket_id = current_user.email.split("@")[0]
    if supermercado_id != user_supermarket_id:
        security_logger.warning(
            f"Intento de IDOR detectado. Supermercado '{user_supermarket_id}' "
            f"intento realizar carga masiva para '{supermercado_id}'"
        )
        raise HTTPException(
            status_code=403,
            detail="Acceso denegado. No autorizado a modificar este supermercado.",
        )

    if not file.filename.endswith(".csv"):
        return JSONResponse(status_code=400, content={"error": "Solo se permiten archivos en formato CSV."})

    try:
        contents = await file.read()
        decoded = contents.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(decoded))

        actual_headers = set(csv_reader.fieldnames or [])
        header_map = {}
        for h in actual_headers:
            h_lower = h.lower().strip()
            if h_lower in ["codigo_barras", "barcode", "ean", "code"]:
                header_map["codigo_barras"] = h
            elif h_lower in ["nombre", "name", "producto", "product"]:
                header_map["nombre"] = h
            elif h_lower in ["precio", "price", "rate"]:
                header_map["precio"] = h
            elif h_lower in ["categoria_fse", "category", "categoria"]:
                header_map["categoria_fse"] = h
            elif h_lower in ["palabras_clave_ocr", "keywords", "keywords_ocr", "palabras_clave"]:
                header_map["palabras_clave_ocr"] = h

        if len({"codigo_barras", "nombre", "precio"}.intersection(header_map.keys())) < 3:
            return JSONResponse(
                status_code=400,
                content={"error": f"El CSV debe contener 'codigo_barras', 'nombre' y 'precio'. "
                         f"Cabeceras detectadas: {list(actual_headers)}"},
            )

        existing_products = db.query(PSModel).filter(
            PSModel.supermercado_id == supermercado_id
        ).all()
        existing_map = {p.codigo_barras: p for p in existing_products}

        new_objects = []
        updated_count = created_count = 0
        warnings = []

        for idx, row in enumerate(csv_reader, start=1):
            raw_barcode = row.get(header_map.get("codigo_barras"))
            raw_name = row.get(header_map.get("nombre"))
            raw_price = row.get(header_map.get("precio"))
            raw_category = row.get(header_map.get("categoria_fse")) if "categoria_fse" in header_map else None
            raw_keywords = row.get(header_map.get("palabras_clave_ocr")) if "palabras_clave_ocr" in header_map else None

            if not raw_barcode or not raw_barcode.strip():
                warnings.append(f"Fila {idx}: Código de barras vacío, fila omitida.")
                continue
            if not raw_name or not raw_name.strip():
                warnings.append(f"Fila {idx}: Nombre de producto vacío, fila omitida.")
                continue

            try:
                price_str = raw_price.replace(",", ".").strip() if raw_price else "0"
                price_val = float(price_str)
                if price_val <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                warnings.append(f"Fila {idx}: Precio inválido ('{raw_price}'), fila omitida.")
                continue

            barcode = raw_barcode.strip()
            nombre = raw_name.strip()
            categoria_fse = raw_category.strip() if raw_category else None

            if not re.match(r"^\d+$", barcode):
                warnings.append(f"Fila {idx}: Código '{raw_barcode}' inválido (solo dígitos), omitido.")
                continue
            if not re.match(r"^[\w\s\d.,\-()áéíóúÁÉÍÓÚñÑüÜ%/]+$", nombre):
                warnings.append(f"Fila {idx}: Nombre '{raw_name}' contiene caracteres inválidos, omitido.")
                continue

            keywords = []
            if raw_keywords:
                keywords = [k.strip() for k in re.split(r"[;,]", raw_keywords) if k.strip()]

            if barcode in existing_map:
                ei = existing_map[barcode]
                ei.nombre = nombre
                ei.precio = price_val
                ei.categoria_fse = categoria_fse
                ei.palabras_clave_ocr = keywords
                updated_count += 1
            else:
                new_objects.append(PSModel(
                    supermercado_id=supermercado_id,
                    codigo_barras=barcode,
                    nombre=nombre,
                    precio=price_val,
                    categoria_fse=categoria_fse,
                    palabras_clave_ocr=keywords,
                ))
                created_count += 1

        if new_objects:
            db.bulk_save_objects(new_objects)
        db.commit()

        return {
            "status": "success",
            "message": f"Carga masiva completada para '{supermercado_id}'.",
            "creados": created_count,
            "actualizados": updated_count,
            "total_procesado": created_count + updated_count,
            "advertencias": warnings,
        }
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500,
                            content={"error": f"Error crítico al procesar la carga batch: {str(e)}"})


@router.post(
    "/api/supermercado/cargar-semilla",
    summary="Cargar Catálogo Semilla para el Supermercado",
    tags=["[SUPERMERCADO] Catálogos"],
)
async def cargar_catalogo_semilla(request: Request, db: Session = Depends(get_db)):
    user = await get_current_supermercado(request, db)
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    supermarket_id = user.email.split("@")[0]
    category_prices = {
        "dairy": 1.25, "beverages": 1.75, "alcoholic-beverages": 2.10, "oils": 4.95,
        "condiments": 0.85, "sweeteners": 1.20, "grains": 1.40, "pasta": 1.10,
        "bread": 1.25, "cereals": 2.30, "bakery": 1.80, "snack": 1.50,
        "chocolate": 2.45, "cocoa": 2.75, "coffee": 2.85, "tea": 1.35,
        "sauce": 1.15, "fish": 2.25, "meat": 3.45, "eggs": 2.50,
        "frozen": 2.95, "hygiene": 2.20, "baby": 6.80, "cleaning": 3.50,
    }

    imported_count = 0
    for barcode, name, category in SEED_CATALOG:
        existing = db.query(PSModel).filter(
            PSModel.supermercado_id == supermarket_id,
            PSModel.codigo_barras == barcode,
        ).first()
        if not existing:
            words = [w.upper() for w in name.split()
                     if len(w) > 2 and w.lower() not in ["con", "para", "del", "una", "los", "las", "pack"]]
            db.add(PSModel(
                supermercado_id=supermarket_id,
                codigo_barras=barcode,
                nombre=name,
                precio=category_prices.get(category, 1.50),
                categoria_fse=category,
                palabras_clave_ocr=words,
            ))
            imported_count += 1

    if imported_count > 0:
        db.commit()

    return {
        "status": "success",
        "message": f"Se han importado {imported_count} productos semilla al catálogo de {supermarket_id}.",
    }


@router.get(
    "/api/supermercado/dashboard-data",
    summary="Obtener Datos de Facturación de Supermercado",
    tags=["[SUPERMERCADO] Catálogos"],
)
async def get_supermercado_dashboard_data(request: Request, db: Session = Depends(get_db)):
    user = await get_current_supermercado(request, db)
    if not user:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})

    supermarket_id = user.email.split("@")[0]
    products = db.query(PSModel).filter(PSModel.supermercado_id == supermarket_id).all()
    sales = db.query(ATModel).filter(
        ATModel.supermercado_id == supermarket_id
    ).order_by(ATModel.timestamp.desc()).all()
    total_billed = sum(float(s.total) for s in sales if s.estado == "APPROVED")

    return {
        "supermercado_id": supermarket_id,
        "products": [
            {"id": p.id, "codigo_barras": p.codigo_barras, "nombre": p.nombre,
             "precio": float(p.precio), "categoria_fse": p.categoria_fse,
             "palabras_clave_ocr": p.palabras_clave_ocr}
            for p in products
        ],
        "sales": [
            {"id": str(s.id), "total": float(s.total),
             "timestamp": s.timestamp.isoformat(), "estado": s.estado}
            for s in sales
        ],
        "kpis": {"total_billed": total_billed, "total_sales_count": len(sales)},
    }


@router.get("/supermercado/dashboard", response_class=HTMLResponse,
            summary="Dashboard de Mantenimiento de Supermercado",
            tags=["[SUPERMERCADO] Catálogos"])
async def supermercado_dashboard(request: Request, db: Session = Depends(get_db)):
    user = await get_current_supermercado(request, db)
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
    html_path = BASE_DIR / "templates" / "supermercado_dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
