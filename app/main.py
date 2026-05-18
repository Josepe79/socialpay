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

# Catálogo local — fuente principal de búsqueda (instantáneo, sin depender de OFF)
LOCAL_CATALOG = [
    # Lácteos
    {"name": "Leche entera Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Leche semidesnatada Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Leche desnatada Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Leche sin lactosa Hacendado", "categories_tags": ["en:dairy", "en:milk"]},
    {"name": "Yogur natural Danone", "categories_tags": ["en:dairy", "en:yogurts"]},
    {"name": "Yogur griego Fage 0%", "categories_tags": ["en:dairy", "en:yogurts"]},
    {"name": "Mantequilla President", "categories_tags": ["en:dairy"]},
    {"name": "Queso manchego El Ventero", "categories_tags": ["en:dairy", "en:cheese"]},
    {"name": "Queso fresco Hacendado", "categories_tags": ["en:dairy", "en:cheese"]},
    {"name": "Nata para cocinar Hacendado", "categories_tags": ["en:dairy"]},
    # Bebidas
    {"name": "Coca-Cola original 1.5L", "categories_tags": ["en:beverages", "en:soda"]},
    {"name": "Coca-Cola Zero azúcar 1.5L", "categories_tags": ["en:beverages", "en:soda"]},
    {"name": "Agua mineral Bezoya 1.5L", "categories_tags": ["en:beverages", "en:water"]},
    {"name": "Agua mineral Hacendado 6x1.5L", "categories_tags": ["en:beverages", "en:water"]},
    {"name": "Zumo de naranja Don Simón 1L", "categories_tags": ["en:beverages", "en:juice"]},
    {"name": "Cerveza Estrella Damm lata", "categories_tags": ["en:alcoholic-beverages", "en:beer"]},
    {"name": "Vino tinto Marqués de Cáceres", "categories_tags": ["en:alcoholic-beverages", "en:wine"]},
    # Aceites y condimentos
    {"name": "Aceite de oliva virgen extra Carbonell", "categories_tags": ["en:oils", "en:olive-oil"]},
    {"name": "Aceite de girasol Hacendado", "categories_tags": ["en:oils"]},
    {"name": "Vinagre de Jerez Hacendado", "categories_tags": ["en:condiments"]},
    {"name": "Sal marina Hacendado 1kg", "categories_tags": ["en:condiments"]},
    {"name": "Azúcar blanco Hacendado 1kg", "categories_tags": ["en:sweeteners"]},
    {"name": "Azúcar moreno Hacendado 1kg", "categories_tags": ["en:sweeteners"]},
    # Pasta, arroz y cereales
    {"name": "Arroz redondo Hacendado 1kg", "categories_tags": ["en:grains", "en:rice"]},
    {"name": "Pasta espagueti Barilla nº5", "categories_tags": ["en:pasta"]},
    {"name": "Pasta macarrones Gallo", "categories_tags": ["en:pasta"]},
    {"name": "Pasta fusilli Hacendado", "categories_tags": ["en:pasta"]},
    {"name": "Harina de trigo Gallo 1kg", "categories_tags": ["en:bread", "en:flour"]},
    {"name": "Cereales Corn Flakes Kellogg's", "categories_tags": ["en:bread", "en:cereals"]},
    {"name": "Avena Quaker Oats 500g", "categories_tags": ["en:bread", "en:cereals"]},
    # Pan y bollería
    {"name": "Pan de molde Bimbo blanco", "categories_tags": ["en:bread", "en:bakery"]},
    {"name": "Pan de molde integral Bimbo", "categories_tags": ["en:bread", "en:bakery"]},
    {"name": "Croissants Hacendado pack 4", "categories_tags": ["en:bread", "en:bakery"]},
    # Galletas y snacks
    {"name": "Galletas María Fontaneda 800g", "categories_tags": ["en:snack", "en:biscuits"]},
    {"name": "Galletas Oreo pack", "categories_tags": ["en:snack", "en:biscuits", "en:chocolate"]},
    {"name": "Galletas digestive McVitie's", "categories_tags": ["en:snack", "en:biscuits"]},
    {"name": "Patatas fritas Lay's clásicas", "categories_tags": ["en:snack", "en:chip", "en:crisps"]},
    {"name": "Patatas fritas Ruffles queso", "categories_tags": ["en:snack", "en:chip"]},
    {"name": "Nachos Hacendado con sal", "categories_tags": ["en:snack", "en:chip"]},
    {"name": "Cacahuetes Hacendado tostados", "categories_tags": ["en:snack", "en:nuts"]},
    # Chocolate y dulces
    {"name": "Nutella 400g", "categories_tags": ["en:chocolate", "en:spreads"]},
    {"name": "ColaCao original 400g", "categories_tags": ["en:beverages", "en:cocoa"]},
    {"name": "Nesquik chocolate 400g", "categories_tags": ["en:beverages", "en:cocoa"]},
    {"name": "Chocolate negro Lindt 85%", "categories_tags": ["en:chocolate", "en:candy"]},
    {"name": "Ferrero Rocher 16u", "categories_tags": ["en:chocolate", "en:candy"]},
    {"name": "Kit Kat Nestlé 2u", "categories_tags": ["en:chocolate", "en:candy"]},
    {"name": "Kinder Bueno 2u", "categories_tags": ["en:chocolate", "en:candy"]},
    # Café e infusiones
    {"name": "Café molido Marcilla natural", "categories_tags": ["en:coffee", "en:beverages"]},
    {"name": "Café Nescafé Classic", "categories_tags": ["en:coffee", "en:beverages"]},
    {"name": "Té verde Hacendado", "categories_tags": ["en:coffee", "en:beverages"]},
    # Salsas y conservas
    {"name": "Tomate frito Hacendado 400g", "categories_tags": ["en:sauce", "en:condiment"]},
    {"name": "Ketchup Heinz 460g", "categories_tags": ["en:sauce", "en:condiment", "en:ketchup"]},
    {"name": "Mayonesa Hellmann's 430ml", "categories_tags": ["en:sauce", "en:condiment", "en:mayo"]},
    {"name": "Atún en aceite Calvo pack 3", "categories_tags": ["en:meat", "en:fish"]},
    {"name": "Sardinas en aceite Hacendado", "categories_tags": ["en:meat", "en:fish"]},
    {"name": "Tomate triturado Hacendado 400g", "categories_tags": ["en:sauce"]},
    # Carne y embutidos
    {"name": "Jamón serrano lonchas Campofrío", "categories_tags": ["en:meat"]},
    {"name": "Pechuga de pavo Campofrío", "categories_tags": ["en:meat", "en:poultry"]},
    {"name": "Chorizo extra El Pozo", "categories_tags": ["en:meat"]},
    {"name": "Salchichas Frankfurt Hacendado 4u", "categories_tags": ["en:meat"]},
    {"name": "Huevos camperos Hacendado 12u", "categories_tags": ["en:egg"]},
    # Congelados
    {"name": "Pizza margarita Hacendado", "categories_tags": ["en:frozen"]},
    {"name": "Pizza 4 quesos Dr. Oetker", "categories_tags": ["en:frozen"]},
    {"name": "Guisantes congelados Hacendado 1kg", "categories_tags": ["en:frozen", "en:vegetables"]},
    {"name": "Patatas fritas congeladas Hacendado", "categories_tags": ["en:frozen"]},
    {"name": "Helado Magnum classic", "categories_tags": ["en:frozen", "en:dessert"]},
    {"name": "Helado Häagen-Dazs vainilla", "categories_tags": ["en:frozen", "en:dessert"]},
    # Higiene personal
    {"name": "Champú Pantene Pro-V", "categories_tags": ["en:hygiene"]},
    {"name": "Gel de ducha Sanex zero", "categories_tags": ["en:hygiene"]},
    {"name": "Desodorante Dove spray", "categories_tags": ["en:hygiene"]},
    {"name": "Pasta de dientes Colgate triple", "categories_tags": ["en:hygiene"]},
    {"name": "Jabón de manos Sanex", "categories_tags": ["en:hygiene"]},
    {"name": "Papel higiénico Scottex 12u", "categories_tags": ["en:hygiene"]},
    {"name": "Papel higiénico Hacendado 12u", "categories_tags": ["en:hygiene"]},
    {"name": "Toallitas húmedas Hacendado 72u", "categories_tags": ["en:hygiene"]},
    # Bebé
    {"name": "Pañales Dodot talla 4 46u", "categories_tags": ["en:hygiene", "en:baby"]},
    {"name": "Pañales Dodot talla 3 56u", "categories_tags": ["en:hygiene", "en:baby"]},
    {"name": "Pañales Huggies talla 4", "categories_tags": ["en:hygiene", "en:baby"]},
    {"name": "Pañales Hacendado talla 4 40u", "categories_tags": ["en:hygiene", "en:baby"]},
    {"name": "Toallitas Dodot sensitive 54u", "categories_tags": ["en:hygiene", "en:baby"]},
    {"name": "Leche de inicio Nestlé NAN 1", "categories_tags": ["en:dairy", "en:baby"]},
    {"name": "Potito Nestlé pollo con arroz", "categories_tags": ["en:baby"]},
    # Limpieza del hogar
    {"name": "Detergente Ariel polvo 40 lavados", "categories_tags": ["en:hygiene"]},
    {"name": "Detergente Persil líquido 30 lavados", "categories_tags": ["en:hygiene"]},
    {"name": "Suavizante Mimosín azul 60 lavados", "categories_tags": ["en:hygiene"]},
    {"name": "Limpiahogar Hacendado multiusos", "categories_tags": ["en:hygiene"]},
    {"name": "Lejía Estrella KH-7", "categories_tags": ["en:hygiene"]},
    {"name": "Bayetas Scotch-Brite pack 2", "categories_tags": ["en:hygiene"]},
]

def search_local_catalog(query: str):
    """Búsqueda local por substring, insensible a mayúsculas y acentos."""
    import unicodedata
    def normalize(s):
        return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()
    q = normalize(query.strip())
    return [p for p in LOCAL_CATALOG if q in normalize(p["name"])]

@app.get("/api/search")
async def search_products(q: str):
    """Busca en catálogo local (instantáneo) y enriquece con OFF si responde rápido."""
    if not q or len(q.strip()) < 2:
        return {"products": []}

    # Siempre buscamos en local primero (instantáneo)
    local_results = search_local_catalog(q)

    # Intentamos OFF con timeout corto para enriquecer resultados
    try:
        headers = {"User-Agent": "SocialPayMVP - Android - Version 1.0 - www.jepco.es"}
        params = {
            "search_terms": q.strip(),
            "search_simple": "1",
            "action": "process",
            "json": "1",
            "page_size": "6",
        }
        response = requests.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params=params,
            headers=headers,
            timeout=2  # Timeout muy corto: si tarda más, usamos solo local
        )
        if response.status_code == 200:
            data = response.json()
            off_results = []
            local_names = {p["name"].lower() for p in local_results}
            for p in data.get("products", []):
                name = (p.get("product_name_es") or p.get("product_name") or "").strip()
                if name and name.lower() not in local_names:
                    off_results.append({"name": name, "categories_tags": p.get("categories_tags", [])})
            # Locales primero, luego los de OFF que no sean duplicados
            return {"products": local_results + off_results, "source": "combined"}
    except Exception:
        pass  # Timeout o error: solo devolvemos locales

    return {"products": local_results, "source": "local"}

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
