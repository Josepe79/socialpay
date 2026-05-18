from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import shutil
import sys
import os
import uuid
import json
import requests
from datetime import datetime

# Add parent directory to path so logic module can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logic.matcher import ProductMatcher

app = FastAPI(title="SocialPay MVP")

# Paths setup
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

matcher = ProductMatcher()

# Base de datos simulada en memoria para auditoría FSE+
db_auditoria = []

@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@app.post("/scan-product")
async def scan_product(barcode: str = Form(...)):
    """Simulates scanning a product with Open Food Facts"""
    info = matcher.get_product_info(barcode)
    return info

@app.post("/scan/manual")
async def scan_manual(product_name: str = Form(...), price: float = Form(...)):
    """Logs a manually searched product added to the cart."""
    return {"status": "success", "name": product_name, "price": price}

# Catálogo local de fallback para cuando OFF no está disponible
LOCAL_CATALOG = [
    {"name": "Leche entera Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Leche semi Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Leche desnatada Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Yogur natural Danone", "categories_tags": ["en:dairy", "en:yogurts"]},
    {"name": "Mantequilla President", "categories_tags": ["en:dairy"]},
    {"name": "Queso manchego El Ventero", "categories_tags": ["en:dairy", "en:cheese"]},
    {"name": "Coca-Cola original 1.5L", "categories_tags": ["en:beverages", "en:soda"]},
    {"name": "Coca-Cola Zero azúcar", "categories_tags": ["en:beverages", "en:soda"]},
    {"name": "Agua mineral Bezoya 1.5L", "categories_tags": ["en:beverages", "en:water"]},
    {"name": "Zumo de naranja Don Simón", "categories_tags": ["en:beverages", "en:juice"]},
    {"name": "Cerveza Estrella Damm", "categories_tags": ["en:alcoholic-beverages", "en:beer"]},
    {"name": "Vino tinto Marqués de Cáceres", "categories_tags": ["en:alcoholic-beverages", "en:wine"]},
    {"name": "Aceite de oliva virgen extra Carbonell", "categories_tags": ["en:oils", "en:olive-oil"]},
    {"name": "Aceite de girasol Hacendado", "categories_tags": ["en:oils"]},
    {"name": "Arroz redondo Hacendado", "categories_tags": ["en:grains", "en:rice"]},
    {"name": "Pasta espagueti Barilla nº5", "categories_tags": ["en:pasta"]},
    {"name": "Pasta macarrones Gallo", "categories_tags": ["en:pasta"]},
    {"name": "Harina de trigo Gallo", "categories_tags": ["en:bread", "en:flour"]},
    {"name": "Pan de molde Bimbo", "categories_tags": ["en:bread", "en:bakery"]},
    {"name": "Pan de molde integral Bimbo", "categories_tags": ["en:bread", "en:bakery"]},
    {"name": "Galletas María Fontaneda", "categories_tags": ["en:snack", "en:biscuits"]},
    {"name": "Galletas Oreo", "categories_tags": ["en:snack", "en:biscuits", "en:chocolate"]},
    {"name": "Nutella 400g", "categories_tags": ["en:chocolate", "en:spreads"]},
    {"name": "ColaCao original 400g", "categories_tags": ["en:beverages", "en:cocoa"]},
    {"name": "Nesquik chocolate 400g", "categories_tags": ["en:beverages", "en:cocoa"]},
    {"name": "Café molido Marcilla natural", "categories_tags": ["en:coffee", "en:beverages"]},
    {"name": "Café Nescafé Classic", "categories_tags": ["en:coffee", "en:beverages"]},
    {"name": "Azúcar blanco Hacendado 1kg", "categories_tags": ["en:sweeteners"]},
    {"name": "Sal marina Hacendado", "categories_tags": ["en:condiments"]},
    {"name": "Tomate frito Hacendado", "categories_tags": ["en:sauce", "en:condiment"]},
    {"name": "Ketchup Heinz", "categories_tags": ["en:sauce", "en:condiment", "en:ketchup"]},
    {"name": "Mayonesa Hellmann's", "categories_tags": ["en:sauce", "en:condiment", "en:mayo"]},
    {"name": "Atún en aceite Calvo pack 3", "categories_tags": ["en:meat", "en:fish"]},
    {"name": "Sardinas en aceite Hacendado", "categories_tags": ["en:meat", "en:fish"]},
    {"name": "Jamón serrano lonchas Campofrío", "categories_tags": ["en:meat"]},
    {"name": "Pechuga de pavo Campofrío", "categories_tags": ["en:meat", "en:poultry"]},
    {"name": "Huevos camperos Hacendado 12u", "categories_tags": ["en:egg"]},
    {"name": "Patatas fritas Lay's clásicas", "categories_tags": ["en:snack", "en:chip", "en:crisps"]},
    {"name": "Patatas fritas Ruffles queso", "categories_tags": ["en:snack", "en:chip"]},
    {"name": "Cacahuetes Hacendado tostados", "categories_tags": ["en:snack", "en:nuts"]},
    {"name": "Chocolate negro Lindt 85%", "categories_tags": ["en:chocolate", "en:candy"]},
    {"name": "Ferrero Rocher 16u", "categories_tags": ["en:chocolate", "en:candy", "en:sweet"]},
    {"name": "Detergente Ariel polvo 40 lavados", "categories_tags": ["en:hygiene"]},
    {"name": "Suavizante Mimosín azul", "categories_tags": ["en:hygiene"]},
    {"name": "Papel higiénico Scottex 12u", "categories_tags": ["en:hygiene"]},
    {"name": "Champú Pantene Pro-V", "categories_tags": ["en:hygiene"]},
    {"name": "Gel de ducha Sanex", "categories_tags": ["en:hygiene"]},
    {"name": "Pizza margarita Hacendado", "categories_tags": ["en:frozen"]},
    {"name": "Guisantes congelados Hacendado", "categories_tags": ["en:frozen", "en:vegetables"]},
    {"name": "Helado Magnum classic", "categories_tags": ["en:frozen", "en:dessert"]},
]

def search_local_catalog(query: str):
    """Búsqueda local por substring, insensible a mayúsculas."""
    q = query.lower().strip()
    return [p for p in LOCAL_CATALOG if q in p["name"].lower()]

@app.get("/api/search")
async def search_products(q: str):
    """Proxy de búsqueda. Intenta OFF primero, usa catálogo local si falla."""
    if not q or len(q.strip()) < 2:
        return {"products": []}
    try:
        headers = {"User-Agent": "SocialPayMVP - Android - Version 1.0 - www.jepco.es"}
        params = {
            "search_terms": q.strip(),
            "search_simple": "1",
            "action": "process",
            "json": "1",
            "page_size": "10",
        }
        response = requests.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params=params,
            headers=headers,
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            products = []
            for p in data.get("products", []):
                name = (p.get("product_name_es") or p.get("product_name") or "").strip()
                if name:
                    products.append({"name": name, "categories_tags": p.get("categories_tags", [])})
            if products:
                return {"products": products, "source": "off"}
        # Fallback catálogo local
        return {"products": search_local_catalog(q), "source": "local"}
    except Exception:
        return {"products": search_local_catalog(q), "source": "local"}

@app.post("/upload-ticket")
async def upload_ticket(
    ticket: UploadFile = File(...), 
    cart_total: float = Form(...), 
    cart_items: str = Form(...),
    supermarket: str = Form(...)
):
    """Handles ticket upload, simulated OCR, and matching."""
    file_path = UPLOAD_DIR / ticket.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(ticket.file, buffer)
    
    # Simulate OCR extracting text from image
    # For MVP, we'll pretend the OCR extracted text that contains the cart_total
    simulated_ocr_text = f"Supermercado Ejemplo\nPan: 1.00\nLeche: 1.20\nTOTAL: {cart_total:.2f}\nGracias por su compra"
    
    match_result = matcher.match_ticket_vs_cart(cart_total, simulated_ocr_text)
    
    if match_result:
        # Generar registro inmutable de auditoría para FSE+
        try:
            parsed_cart_items = json.loads(cart_items)
        except json.JSONDecodeError:
            parsed_cart_items = []

        audit_record = {
            "transaction_id": str(uuid.uuid4()),
            "user_id": "USR-99X",
            "supermarket": supermarket,
            "timestamp": datetime.now().isoformat(),
            "cart_snapshot": parsed_cart_items,
            "ticket_image_path": str(file_path.absolute()),
            "status": "AUDITED_AND_APPROVED"
        }
        db_auditoria.append(audit_record)

        return {"status": "success", "message": "Ticket validado correctamente"}
    else:
        return {"status": "error", "message": "El total no coincide con el ticket"}

@app.get("/api/admin/audit-logs")
async def get_audit_logs():
    """Panel de control simulado para inspectores de la UE."""
    return JSONResponse(content={"total_records": len(db_auditoria), "logs": db_auditoria})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard():
    """Vista HTML del panel de control de auditoría FSE+."""
    html_path = BASE_DIR / "templates" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
