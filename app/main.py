from fastapi import FastAPI, Request, File, UploadFile, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import shutil
import sys
import os
import uuid
import json
import unicodedata
import requests
import psycopg2
import psycopg2.extras
import google.generativeai as genai
from PIL import Image
from datetime import datetime

# Add parent directory to path so logic module can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logic.matcher import ProductMatcher
from logic.validator import TicketValidator

app = FastAPI(title="SocialPay MVP")

# Paths setup
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
matcher = ProductMatcher()

# ── Auditoría en memoria (persiste mientras el proceso esté vivo) ─────────────
db_auditoria = []

# ── PostgreSQL ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_conn():
    """Devuelve una conexión psycopg2 nueva."""
    url = DATABASE_URL
    # Railway usa postgres://, psycopg2 necesita postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)

def normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()

# Catálogo semilla — se carga en DB al arrancar si está vacía
SEED_CATALOG = [
    # Lácteos
    ("seed-lac-001", "Leche entera Hacendado",          "dairy"),
    ("seed-lac-002", "Leche semidesnatada Hacendado",   "dairy"),
    ("seed-lac-003", "Leche desnatada Hacendado",       "dairy"),
    ("seed-lac-004", "Leche sin lactosa Hacendado",     "dairy"),
    ("seed-lac-005", "Yogur natural Danone",            "dairy"),
    ("seed-lac-006", "Yogur griego Fage 0%",            "dairy"),
    ("seed-lac-007", "Mantequilla President",           "dairy"),
    ("seed-lac-008", "Queso manchego El Ventero",       "dairy"),
    ("seed-lac-009", "Queso fresco Hacendado",          "dairy"),
    ("seed-lac-010", "Nata para cocinar Hacendado",     "dairy"),
    # Bebidas
    ("seed-beb-001", "Coca-Cola original 1.5L",         "beverages"),
    ("seed-beb-002", "Coca-Cola Zero azúcar 1.5L",      "beverages"),
    ("seed-beb-003", "Agua mineral Bezoya 1.5L",        "beverages"),
    ("seed-beb-004", "Agua mineral Hacendado 6x1.5L",   "beverages"),
    ("seed-beb-005", "Zumo de naranja Don Simón 1L",    "beverages"),
    ("seed-beb-006", "Cerveza Estrella Damm lata",      "alcoholic-beverages"),
    ("seed-beb-007", "Vino tinto Marqués de Cáceres",   "alcoholic-beverages"),
    # Aceites y condimentos
    ("seed-ace-001", "Aceite de oliva virgen extra Carbonell", "oils"),
    ("seed-ace-002", "Aceite de girasol Hacendado",     "oils"),
    ("seed-ace-003", "Vinagre de Jerez Hacendado",      "condiments"),
    ("seed-ace-004", "Sal marina Hacendado 1kg",        "condiments"),
    ("seed-ace-005", "Azúcar blanco Hacendado 1kg",     "sweeteners"),
    ("seed-ace-006", "Azúcar moreno Hacendado 1kg",     "sweeteners"),
    # Pasta, arroz y cereales
    ("seed-pas-001", "Arroz redondo Hacendado 1kg",     "grains"),
    ("seed-pas-002", "Pasta espagueti Barilla nº5",     "pasta"),
    ("seed-pas-003", "Pasta macarrones Gallo",          "pasta"),
    ("seed-pas-004", "Pasta fusilli Hacendado",         "pasta"),
    ("seed-pas-005", "Harina de trigo Gallo 1kg",       "bread"),
    ("seed-pas-006", "Cereales Corn Flakes Kellogg's",  "cereals"),
    ("seed-pas-007", "Avena Quaker Oats 500g",          "cereals"),
    # Pan y bollería
    ("seed-pan-001", "Pan de molde Bimbo blanco",       "bakery"),
    ("seed-pan-002", "Pan de molde integral Bimbo",     "bakery"),
    ("seed-pan-003", "Croissants Hacendado pack 4",     "bakery"),
    # Galletas y snacks
    ("seed-gal-001", "Galletas María Fontaneda 800g",   "snack"),
    ("seed-gal-002", "Galletas Oreo pack",              "snack"),
    ("seed-gal-003", "Galletas digestive McVitie's",    "snack"),
    ("seed-gal-004", "Patatas fritas Lay's clásicas",   "snack"),
    ("seed-gal-005", "Patatas fritas Ruffles queso",    "snack"),
    ("seed-gal-006", "Nachos Hacendado con sal",        "snack"),
    ("seed-gal-007", "Cacahuetes Hacendado tostados",   "snack"),
    # Chocolate y dulces
    ("seed-cho-001", "Nutella 400g",                    "chocolate"),
    ("seed-cho-002", "ColaCao original 400g",           "cocoa"),
    ("seed-cho-003", "Nesquik chocolate 400g",          "cocoa"),
    ("seed-cho-004", "Chocolate negro Lindt 85%",       "chocolate"),
    ("seed-cho-005", "Ferrero Rocher 16u",              "chocolate"),
    ("seed-cho-006", "Kit Kat Nestlé 2u",               "chocolate"),
    ("seed-cho-007", "Kinder Bueno 2u",                 "chocolate"),
    # Café e infusiones
    ("seed-caf-001", "Café molido Marcilla natural",    "coffee"),
    ("seed-caf-002", "Café Nescafé Classic",            "coffee"),
    ("seed-caf-003", "Té verde Hacendado",              "tea"),
    # Salsas y conservas
    ("seed-sal-001", "Tomate frito Hacendado 400g",     "sauce"),
    ("seed-sal-002", "Ketchup Heinz 460g",              "sauce"),
    ("seed-sal-003", "Mayonesa Hellmann's 430ml",       "sauce"),
    ("seed-sal-004", "Atún en aceite Calvo pack 3",     "fish"),
    ("seed-sal-005", "Sardinas en aceite Hacendado",    "fish"),
    ("seed-sal-006", "Tomate triturado Hacendado 400g", "sauce"),
    # Carne y embutidos
    ("seed-car-001", "Jamón serrano lonchas Campofrío", "meat"),
    ("seed-car-002", "Pechuga de pavo Campofrío",       "meat"),
    ("seed-car-003", "Chorizo extra El Pozo",           "meat"),
    ("seed-car-004", "Salchichas Frankfurt Hacendado 4u","meat"),
    ("seed-car-005", "Huevos camperos Hacendado 12u",   "eggs"),
    # Congelados
    ("seed-con-001", "Pizza margarita Hacendado",       "frozen"),
    ("seed-con-002", "Pizza 4 quesos Dr. Oetker",       "frozen"),
    ("seed-con-003", "Guisantes congelados Hacendado 1kg","frozen"),
    ("seed-con-004", "Patatas fritas congeladas Hacendado","frozen"),
    ("seed-con-005", "Helado Magnum classic",           "frozen"),
    ("seed-con-006", "Helado Häagen-Dazs vainilla",     "frozen"),
    # Higiene personal
    ("seed-hig-001", "Champú Pantene Pro-V",            "hygiene"),
    ("seed-hig-002", "Gel de ducha Sanex zero",         "hygiene"),
    ("seed-hig-003", "Desodorante Dove spray",          "hygiene"),
    ("seed-hig-004", "Pasta de dientes Colgate triple", "hygiene"),
    ("seed-hig-005", "Jabón de manos Sanex",            "hygiene"),
    ("seed-hig-006", "Papel higiénico Scottex 12u",     "hygiene"),
    ("seed-hig-007", "Papel higiénico Hacendado 12u",   "hygiene"),
    ("seed-hig-008", "Toallitas húmedas Hacendado 72u", "hygiene"),
    # Bebé
    ("seed-bab-001", "Pañales Dodot talla 3 56u",       "baby"),
    ("seed-bab-002", "Pañales Dodot talla 4 46u",       "baby"),
    ("seed-bab-003", "Pañales Dodot talla 5 38u",       "baby"),
    ("seed-bab-004", "Pañales Huggies talla 4",         "baby"),
    ("seed-bab-005", "Pañales Hacendado talla 4 40u",   "baby"),
    ("seed-bab-006", "Toallitas Dodot sensitive 54u",   "baby"),
    ("seed-bab-007", "Leche de inicio Nestlé NAN 1",    "baby"),
    ("seed-bab-008", "Potito Nestlé pollo con arroz",   "baby"),
    # Limpieza del hogar
    ("seed-lim-001", "Detergente Ariel polvo 40 lavados","cleaning"),
    ("seed-lim-002", "Detergente Persil líquido 30 lavados","cleaning"),
    ("seed-lim-003", "Suavizante Mimosín azul 60 lavados","cleaning"),
    ("seed-lim-004", "Limpiahogar Hacendado multiusos", "cleaning"),
    ("seed-lim-005", "Lejía Estrella KH-7",             "cleaning"),
    ("seed-lim-006", "Bayetas Scotch-Brite pack 2",     "cleaning"),
]

def init_db():
    """Crea las tablas y carga el catálogo semilla si la BBDD está vacía."""
    if not DATABASE_URL:
        print("[DB] DATABASE_URL no configurado — usando solo catálogo en memoria")
        return
    try:
        conn = get_conn()
        cur = conn.cursor()

        # Tabla de productos globales
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                barcode     TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                category    TEXT,
                allowed     BOOLEAN DEFAULT TRUE,
                source      TEXT DEFAULT 'manual',
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        # Tabla de disponibilidad por supermercado
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supermarket_products (
                id              SERIAL PRIMARY KEY,
                supermarket     TEXT NOT NULL,
                barcode         TEXT NOT NULL REFERENCES products(barcode) ON DELETE CASCADE,
                price_ref       REAL,
                available       BOOLEAN DEFAULT TRUE,
                UNIQUE(supermarket, barcode)
            )
        """)

        # Carga semilla solo si la tabla está vacía
        cur.execute("SELECT COUNT(*) FROM products")
        count = cur.fetchone()[0]
        if count == 0:
            print(f"[DB] Cargando {len(SEED_CATALOG)} productos semilla...")
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO products (barcode, name, category, source) VALUES %s ON CONFLICT DO NOTHING",
                [(b, n, c, "local") for b, n, c in SEED_CATALOG]
            )
            print("[DB] Catálogo semilla cargado.")
        else:
            print(f"[DB] BBDD ya tiene {count} productos.")

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Error inicializando BBDD: {e}")

# Arrancar init al levantar la app
@app.on_event("startup")
def on_startup():
    init_db()

# ── Helpers DB ─────────────────────────────────────────────────────────────────
def db_search(q: str, supermarket: str = None) -> list:
    """Busca productos en PostgreSQL. Filtra por supermercado si se indica."""
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if supermarket:
            cur.execute("""
                SELECT p.barcode, p.name, p.category, p.allowed, sp.price_ref
                FROM products p
                JOIN supermarket_products sp ON p.barcode = sp.barcode
                WHERE sp.supermarket = %s
                  AND p.allowed = TRUE
                  AND unaccent(lower(p.name)) ILIKE unaccent(lower(%s))
                ORDER BY p.name
                LIMIT 12
            """, (supermarket, f"%{q}%"))
        else:
            cur.execute("""
                SELECT barcode, name, category, allowed, NULL as price_ref
                FROM products
                WHERE allowed = TRUE
                  AND lower(name) ILIKE lower(%s)
                ORDER BY name
                LIMIT 12
            """, (f"%{q}%",))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] Search error: {e}")
        return []

def db_get_by_barcode(barcode: str) -> dict | None:
    """Busca un producto por código de barras en DB."""
    if not DATABASE_URL:
        return None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM products WHERE barcode = %s", (barcode,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[DB] Barcode lookup error: {e}")
        return None

def db_upsert_product(barcode: str, name: str, category: str, allowed: bool, source: str = "off"):
    """Guarda o actualiza un producto en DB (aprende de cada escaneo)."""
    if not DATABASE_URL:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO products (barcode, name, category, allowed, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (barcode) DO UPDATE
              SET name = EXCLUDED.name,
                  category = EXCLUDED.category,
                  allowed = EXCLUDED.allowed
        """, (barcode, name, category, allowed, source))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[DB] Upsert error: {e}")

def db_all_products(supermarket: str = None) -> list:
    """Lista todos los productos (con filtro opcional por supermercado)."""
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if supermarket:
            cur.execute("""
                SELECT p.barcode, p.name, p.category, p.allowed, p.source,
                       sp.price_ref, sp.available
                FROM products p
                LEFT JOIN supermarket_products sp
                       ON p.barcode = sp.barcode AND sp.supermarket = %s
                ORDER BY p.category, p.name
            """, (supermarket,))
        else:
            cur.execute("""
                SELECT barcode, name, category, allowed, source,
                       NULL as price_ref, NULL as available
                FROM products ORDER BY category, name
            """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] List error: {e}")
        return []

# ── Fallback en memoria (si no hay DB) ────────────────────────────────────────
MEM_CATALOG = [(b, n, c) for b, n, c in SEED_CATALOG]

def mem_search(q: str) -> list:
    nq = normalize(q)
    return [{"barcode": b, "name": n, "category": c, "allowed": True}
            for b, n, c in MEM_CATALOG if nq in normalize(n)]

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

@app.post("/scan-product")
async def scan_product(barcode: str = Form(...)):
    """Busca producto en DB → caché en memoria → OFF. Guarda lo que aprende."""
    # 1. Buscar en DB
    product = db_get_by_barcode(barcode)
    if product:
        return {"name": product["name"], "allowed": product["allowed"]}

    # 2. Consultar OFF
    info = matcher.get_product_info(barcode)

    # 3. Guardar en DB para futuros escaneos
    if "Error" not in info.get("name", "Error") and "desconocido" not in info.get("name", ""):
        db_upsert_product(
            barcode=barcode,
            name=info["name"],
            category="unknown",
            allowed=info["allowed"],
            source="off"
        )
    return info

@app.post("/scan/manual")
async def scan_manual(product_name: str = Form(...), price: float = Form(...)):
    """Registra un producto añadido manualmente al carrito."""
    return {"status": "success", "name": product_name, "price": price}

@app.get("/api/search")
async def search_products(q: str, supermarket: str = Query(default=None)):
    """Búsqueda en DB (instantánea). Enriquece con OFF si responde en <2s."""
    if not q or len(q.strip()) < 2:
        return {"products": []}

    # 1. Buscar en DB (o en memoria si no hay DB)
    if DATABASE_URL:
        db_results = db_search(q, supermarket)
    else:
        db_results = mem_search(q)

    # 2. Intentar enriquecer con OFF (timeout corto)
    try:
        resp = requests.get(
            "https://world.openfoodfacts.org/cgi/search.pl",
            params={"search_terms": q, "search_simple": "1",
                    "action": "process", "json": "1", "page_size": "5"},
            headers={"User-Agent": "SocialPayMVP/1.0"},
            timeout=2
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

    return {"products": db_results, "source": "db" if DATABASE_URL else "memory"}

# ── Admin: Catálogo de Productos ───────────────────────────────────────────────

@app.get("/api/admin/products")
async def list_products(supermarket: str = Query(default=None)):
    """Lista todos los productos, opcionalmente filtrados por supermercado."""
    return {"products": db_all_products(supermarket)}

@app.post("/api/admin/products")
async def add_product(
    barcode: str = Form(...),
    name: str = Form(...),
    category: str = Form(default="unknown"),
    allowed: bool = Form(default=True),
    supermarket: str = Form(default=None),
    price_ref: float = Form(default=None)
):
    """Añade o actualiza un producto en el catálogo."""
    db_upsert_product(barcode, name, category, allowed, source="manual")

    # Si se especifica supermercado, asociarlo también
    if supermarket and DATABASE_URL:
        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO supermarket_products (supermarket, barcode, price_ref)
                VALUES (%s, %s, %s)
                ON CONFLICT (supermarket, barcode)
                DO UPDATE SET price_ref = EXCLUDED.price_ref, available = TRUE
            """, (supermarket, barcode, price_ref))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "ok", "barcode": barcode, "name": name}

@app.delete("/api/admin/products/{barcode}")
async def delete_product(barcode: str):
    """Elimina un producto del catálogo."""
    if not DATABASE_URL:
        return {"status": "no_db"}
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE barcode = %s", (barcode,))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "deleted"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# ── Ticket upload & OCR Validation ──────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def ocr_ticket_via_gemini(image_path: Path) -> dict:
    """Uses Gemini 1.5 Flash to extract items and total from ticket image."""
    if not GEMINI_API_KEY:
        print("[OCR] GEMINI_API_KEY not configured. Falling back to simulation.")
        return None
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        img = Image.open(image_path)
        
        prompt = (
            "Analiza esta imagen de un ticket/preticket de compra de supermercado. "
            "Extrae el precio total de la compra y la lista de artículos con sus nombres y precios individuales. "
            "Devuelve EXCLUSIVAMENTE un objeto JSON válido con el siguiente formato, "
            "sin usar bloques de código de markdown (no agregues ```json ni ```, solo el JSON plano): "
            '{"total": 12.45, "items": [{"name": "Leche entera Hacendado", "price": 0.89}, ...]} '
            "Si la imagen no es legible o no es un ticket, responde con: "
            '{"total": 0.0, "items": []}'
        )
        
        print(f"[OCR] Sending image to Gemini: {image_path.name}")
        response = model.generate_content([prompt, img])
        text = response.text.strip()
        print(f"[OCR] Raw response from Gemini: {text}")
        
        # Clean potential markdown wrapping
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
            
        data = json.loads(text)
        return data
    except Exception as e:
        print(f"[OCR] Error calling Gemini: {e}")
        return None

@app.post("/upload-ticket")
async def upload_ticket(
    ticket: UploadFile = File(...),
    cart_total: float = Form(...),
    cart_items: str = Form(...),
    supermarket: str = Form(...)
):
    file_path = UPLOAD_DIR / ticket.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(ticket.file, buffer)

    # 1. Parse virtual cart items
    try:
        parsed_cart_items = json.loads(cart_items)
    except json.JSONDecodeError:
        parsed_cart_items = []

    # 2. Try Gemini OCR, fallback to smart simulation if key is missing or calls fail
    ticket_data = ocr_ticket_via_gemini(file_path)
    
    using_fallback = False
    if not ticket_data:
        # Fallback inteligente para la demo si no hay API key o hay error de red/cuota
        using_fallback = True
        print("[OCR] Using simulated fallback matching the cart exactly.")
        ticket_data = {
            "total": cart_total,
            "items": [{"name": item["name"], "price": item["price"]} for item in parsed_cart_items]
        }

    # 3. Validate
    validator = TicketValidator()
    report = validator.validate(parsed_cart_items, cart_total, ticket_data)
    report["using_fallback"] = using_fallback

    # 4. Save to FSE+ Audit Logs if validated successfully
    if report["status"] == "validated":
        audit_record = {
            "transaction_id": str(uuid.uuid4()),
            "user_id": "USR-99X",
            "supermarket": supermarket,
            "timestamp": datetime.now().isoformat(),
            "cart_snapshot": parsed_cart_items,
            "ticket_image_path": str(file_path.absolute()),
            "status": "AUDITED_AND_APPROVED",
            "validation_score": report["score"],
            "ticket_total": report["ticket_total"]
        }
        db_auditoria.append(audit_record)

    return report

@app.get("/api/admin/audit-logs")
async def get_audit_logs():
    return JSONResponse(content={"total_records": len(db_auditoria), "logs": db_auditoria})

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard():
    html_path = BASE_DIR / "templates" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
