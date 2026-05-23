from fastapi import FastAPI, Request, File, UploadFile, Form, Query, Depends
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

from sqlalchemy.orm import Session
from app.database import get_db, engine
from app.models import Base, ProductoSupermercado as PSModel, Usuario as UserModel, AuditoriaTransaccion as ATModel
from pydantic import BaseModel
from typing import List, Optional

import hashlib
import secrets
import pyotp
from datetime import datetime, timedelta

# ── Sistema de Sesiones y Seguridad (MFA / PBKDF2) ────────────────────────────
ADMIN_SESSIONS = {} # Token -> { "user_id": UUID, "mfa_verified": bool, "expires": datetime }

def hash_password(password: str, salt: str = None) -> str:
    """Genera un hash seguro PBKDF2-SHA256 con sal para almacenar contraseñas."""
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_bytes = password.encode('utf-8')
    salt_bytes = salt.encode('utf-8')
    db_hash = hashlib.pbkdf2_hmac('sha256', pwd_bytes, salt_bytes, 100000)
    return f"{salt}:{db_hash.hex()}"

def verify_password(password: str, hashed_password: str) -> bool:
    """Verifica si una contraseña en texto plano coincide con su hash almacenado."""
    if not hashed_password or ":" not in hashed_password:
        return False
    salt, _ = hashed_password.split(":", 1)
    return hash_password(password, salt) == hashed_password

async def get_current_admin(request: Request, db: Session = Depends(get_db)):
    """Dependency para verificar sesión administrativa y MFA activado."""
    session_token = request.cookies.get("session_token")
    if not session_token or session_token not in ADMIN_SESSIONS:
        return None
    
    sess = ADMIN_SESSIONS[session_token]
    if datetime.now() > sess["expires"]:
        ADMIN_SESSIONS.pop(session_token, None)
        return None
        
    if not sess["mfa_verified"]:
        return None
        
    user = db.query(UserModel).filter(UserModel.id == sess["user_id"]).first()
    if not user or user.rol != "admin":
        return None
        
    return user

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
    try:
        # Comprobar desalineación del esquema en la tabla 'usuarios' para auto-sanación
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if "usuarios" in inspector.get_table_names():
            columns = [c["name"] for c in inspector.get_columns("usuarios")]
            required = ["email", "hashed_password", "mfa_secret", "mfa_enabled"]
            missing = [col for col in required if col not in columns]
            if missing:
                print(f"[DB] Columnas faltantes en 'usuarios': {missing}. Recreando tablas para actualizar esquema...")
                # Eliminar todas las tablas administradas por SQLAlchemy
                Base.metadata.drop_all(bind=engine)
                print("[DB] Tablas antiguas eliminadas con éxito.")

        # Crear tablas SQLAlchemy (Usuario, ProductoSupermercado, AuditoriaTransaccion)
        print("[DB] Inicializando tablas SQLAlchemy...")
        Base.metadata.create_all(bind=engine)
        print("[DB] Tablas SQLAlchemy inicializadas.")
    except Exception as e:
        print(f"[DB] Error creando tablas SQLAlchemy: {e}")

    # Semilla del administrador admin@jepco.es con contraseña y MFA por defecto
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        admin_user = db.query(UserModel).filter(UserModel.email == "admin@jepco.es").first()
        if not admin_user:
            print("[DB] Creando usuario administrador semilla: admin@jepco.es...")
            default_password = "JepcoAdmin2026!"
            mfa_secret_key = pyotp.random_base32()
            
            new_admin = UserModel(
                token_anonimo=f"ADMIN-TOKEN-{secrets.token_hex(4).upper()}",
                saldo_disponible=0.00,
                rol="admin",
                email="admin@jepco.es",
                hashed_password=hash_password(default_password),
                mfa_secret=mfa_secret_key,
                mfa_enabled=False # El primer login exigirá vincular el QR
            )
            db.add(new_admin)
            db.commit()
            print(f"[DB] Administrador semilla creado con éxito.")
            print(f"[DB] Contraseña por defecto: {default_password}")
            print(f"[DB] Clave secreta TOTP: {mfa_secret_key}")
        else:
            print("[DB] El administrador admin@jepco.es ya existe.")
            
        # Semilla del beneficiario demo para pruebas
        beneficiary_user = db.query(UserModel).filter(UserModel.token_anonimo == "BENEFICIARIO-DEMO").first()
        if not beneficiary_user:
            print("[DB] Creando usuario beneficiario semilla: BENEFICIARIO-DEMO...")
            new_beneficiary = UserModel(
                token_anonimo="BENEFICIARIO-DEMO",
                saldo_disponible=150.00,
                rol="beneficiario"
            )
            db.add(new_beneficiary)
            db.commit()
            print("[DB] Beneficiario semilla creado con éxito.")
        else:
            print("[DB] El beneficiario BENEFICIARIO-DEMO ya existe.")
            
        db.close()
    except Exception as e:
        print(f"[DB] Error al sembrar usuarios semilla: {e}")

    if not DATABASE_URL:
        print("[DB] DATABASE_URL no configurado — usando solo catálogo en memoria para productos")
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

        # Tabla de mapeos de aprendizaje activo
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ticket_product_mappings (
                id              SERIAL PRIMARY KEY,
                supermarket     TEXT NOT NULL,
                raw_ticket_name TEXT NOT NULL,
                barcode         TEXT NOT NULL REFERENCES products(barcode) ON DELETE CASCADE,
                created_at      TIMESTAMP DEFAULT NOW(),
                UNIQUE(supermarket, raw_ticket_name)
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

def db_get_barcode_by_mapping(supermarket: str, raw_ticket_name: str) -> str | None:
    """Busca si un nombre del ticket ya ha sido mapeado a un código de barras para este supermercado."""
    if not DATABASE_URL:
        return None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT barcode FROM ticket_product_mappings
            WHERE lower(supermarket) = lower(%s) AND lower(raw_ticket_name) = lower(%s)
        """, (supermarket.strip(), raw_ticket_name.strip()))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[DB] Error looking up ticket mapping: {e}")
        return None

def db_save_ticket_mapping(supermarket: str, raw_ticket_name: str, barcode: str):
    """Guarda un mapeo de nombre de ticket a código de barras para un supermercado."""
    if not DATABASE_URL:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ticket_product_mappings (supermarket, raw_ticket_name, barcode)
            VALUES (%s, %s, %s)
            ON CONFLICT (supermarket, raw_ticket_name) DO UPDATE
              SET barcode = EXCLUDED.barcode
        """, (supermarket.strip(), raw_ticket_name.strip(), barcode.strip()))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[DB] Mapeo guardado: [{supermarket}] '{raw_ticket_name}' -> Barcode '{barcode}'")
    except Exception as e:
        print(f"[DB] Error saving ticket mapping: {e}")

# ── Fallback en memoria (si no hay DB) ────────────────────────────────────────
MEM_TICKET_MAPPINGS = {}

def get_ticket_mapping(supermarket: str, raw_ticket_name: str) -> str | None:
    """Busca un mapeo en BBDD o en memoria si no hay BBDD."""
    barcode = db_get_barcode_by_mapping(supermarket, raw_ticket_name)
    if barcode:
        return barcode
    return MEM_TICKET_MAPPINGS.get((supermarket.lower().strip(), raw_ticket_name.lower().strip()))

def save_ticket_mapping(supermarket: str, raw_ticket_name: str, barcode: str):
    """Guarda un mapeo en BBDD o en memoria si no hay BBDD."""
    if DATABASE_URL:
        db_save_ticket_mapping(supermarket, raw_ticket_name, barcode)
    else:
        MEM_TICKET_MAPPINGS[(supermarket.lower().strip(), raw_ticket_name.lower().strip())] = barcode
        print(f"[Memory] Mapeo guardado: [{supermarket}] '{raw_ticket_name}' -> Barcode '{barcode}'")

MEM_CATALOG = [(b, n, c) for b, n, c in SEED_CATALOG]

def mem_search(q: str) -> list:
    nq = normalize(q)
    return [{"barcode": b, "name": n, "category": c, "allowed": True}
            for b, n, c in MEM_CATALOG if nq in normalize(n)]

# ── Routes ─────────────────────────────────────────────────────────────────────

from fastapi.responses import RedirectResponse

@app.get("/", response_class=HTMLResponse)
async def read_root(
    request: Request,
    token: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    token_val = token or request.cookies.get("beneficiary_token")
    user = None
    if token_val:
        user = db.query(UserModel).filter(UserModel.token_anonimo == token_val.strip()).first()
        
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="beneficiary_login.html",
            context={"request": request, "error": "Token no válido o no proporcionado" if token_val else None}
        )
        
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "user": user}
    )
    response.set_cookie(key="beneficiary_token", value=user.token_anonimo, httponly=True)
    return response

@app.post("/beneficiario/login")
async def process_beneficiary_login(
    request: Request,
    token: str = Form(...),
    db: Session = Depends(get_db)
):
    token_val = token.strip()
    user = db.query(UserModel).filter(UserModel.token_anonimo == token_val).first()
    if not user:
        return templates.TemplateResponse(
            request=request,
            name="beneficiary_login.html",
            context={"request": request, "error": "Código de acceso no válido.", "entered_token": token_val}
        )
        
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="beneficiary_token", value=user.token_anonimo, httponly=True)
    return response

@app.get("/beneficiario/logout")
async def beneficiary_logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("beneficiary_token")
    return response

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

# ── Supermercado: Mantenimiento de Productos ──────────────────────────────────

class SupermarketProductSchema(BaseModel):
    supermercado_id: str
    codigo_barras: str
    nombre: str
    precio: float
    categoria_fse: Optional[str] = None
    palabras_clave_ocr: Optional[List[str]] = []

@app.post("/api/supermercado/producto")
async def add_or_update_supermarket_product(
    item: SupermarketProductSchema,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Mantenimiento individual por pantalla para añadir o actualizar un producto del supermercado.
    Exclusivo para el rol 'supermercado'.
    """
    # Verificación de rol: cabecera X-Role o query param 'role' para facilitar pruebas
    x_role = request.headers.get("X-Role") or request.query_params.get("role")
    if x_role != "supermercado":
        return JSONResponse(
            status_code=403,
            content={"error": "Acceso denegado. Se requiere el rol 'supermercado'."}
        )

    # Validaciones de entrada
    if not item.codigo_barras or not item.codigo_barras.strip():
        return JSONResponse(status_code=400, content={"error": "El código de barras es obligatorio."})
    if not item.nombre or not item.nombre.strip():
        return JSONResponse(status_code=400, content={"error": "El nombre es obligatorio."})
    if item.precio <= 0:
        return JSONResponse(status_code=400, content={"error": "El precio debe ser mayor que 0."})

    # Upsert en ProductoSupermercado (SQLAlchemy)
    existing = db.query(PSModel).filter(
        PSModel.supermercado_id == item.supermercado_id,
        PSModel.codigo_barras == item.codigo_barras
    ).first()

    if existing:
        existing.nombre = item.nombre
        existing.precio = item.precio
        existing.categoria_fse = item.categoria_fse
        existing.palabras_clave_ocr = item.palabras_clave_ocr
        db.commit()
        db.refresh(existing)
        status = "updated"
        prod_id = existing.id
    else:
        new_prod = PSModel(
            supermercado_id=item.supermercado_id,
            codigo_barras=item.codigo_barras,
            nombre=item.nombre,
            precio=item.precio,
            categoria_fse=item.categoria_fse,
            palabras_clave_ocr=item.palabras_clave_ocr
        )
        db.add(new_prod)
        db.commit()
        db.refresh(new_prod)
        status = "created"
        prod_id = new_prod.id

    return {
        "status": status,
        "id": prod_id,
        "supermercado_id": item.supermercado_id,
        "codigo_barras": item.codigo_barras,
        "nombre": item.nombre
    }

@app.post("/api/supermercado/upload-batch")
async def upload_batch_products(
    request: Request,
    supermercado_id: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Carga masiva (batch) de productos mediante un archivo CSV.
    Procesa línea por línea, valida datos y actualiza de golpe (Bulk Update/Insert).
    Exclusivo para el rol 'supermercado'.
    """
    # Verificación de rol
    x_role = request.headers.get("X-Role") or request.query_params.get("role")
    if x_role != "supermercado":
        return JSONResponse(
            status_code=403,
            content={"error": "Acceso denegado. Se requiere el rol 'supermercado'."}
        )

    if not file.filename.endswith('.csv'):
        return JSONResponse(status_code=400, content={"error": "Solo se permiten archivos en formato CSV."})

    import csv
    import io
    import re

    try:
        contents = await file.read()
        decoded = contents.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(decoded))

        # Validación e inferencia de cabeceras
        actual_headers = set(csv_reader.fieldnames or [])
        header_map = {}
        for h in actual_headers:
            h_lower = h.lower().strip()
            if h_lower in ['codigo_barras', 'barcode', 'ean', 'code']:
                header_map['codigo_barras'] = h
            elif h_lower in ['nombre', 'name', 'producto', 'product']:
                header_map['nombre'] = h
            elif h_lower in ['precio', 'price', 'rate']:
                header_map['precio'] = h
            elif h_lower in ['categoria_fse', 'category', 'categoria']:
                header_map['categoria_fse'] = h
            elif h_lower in ['palabras_clave_ocr', 'keywords', 'keywords_ocr', 'palabras_clave']:
                header_map['palabras_clave_ocr'] = h

        # Comprobar que tenemos las 3 columnas obligatorias
        if len({'codigo_barras', 'nombre', 'precio'}.intersection(header_map.keys())) < 3:
            return JSONResponse(
                status_code=400,
                content={"error": f"El CSV debe contener al menos las columnas de 'codigo_barras', 'nombre' y 'precio'. Cabeceras detectadas: {list(actual_headers)}"}
            )

        # Cargar todos los productos actuales de este supermercado para evitar consultas N+1
        existing_products = db.query(PSModel).filter(
            PSModel.supermercado_id == supermercado_id
        ).all()
        existing_map = {p.codigo_barras: p for p in existing_products}

        new_objects = []
        updated_count = 0
        created_count = 0
        warnings = []

        for idx, row in enumerate(csv_reader, start=1):
            raw_barcode = row.get(header_map.get('codigo_barras'))
            raw_name = row.get(header_map.get('nombre'))
            raw_price = row.get(header_map.get('precio'))
            raw_category = row.get(header_map.get('categoria_fse')) if 'categoria_fse' in header_map else None
            raw_keywords = row.get(header_map.get('palabras_clave_ocr')) if 'palabras_clave_ocr' in header_map else None

            # Validar campos vacíos
            if not raw_barcode or not raw_barcode.strip():
                warnings.append(f"Fila {idx}: Código de barras vacío, fila omitida.")
                continue
            if not raw_name or not raw_name.strip():
                warnings.append(f"Fila {idx}: Nombre de producto vacío, fila omitida.")
                continue

            # Validar y convertir precio
            try:
                price_str = raw_price.replace(',', '.').strip() if raw_price else "0"
                price_val = float(price_str)
                if price_val <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                warnings.append(f"Fila {idx}: Precio inválido ('{raw_price}'), fila omitida.")
                continue

            barcode = raw_barcode.strip()
            nombre = raw_name.strip()
            categoria_fse = raw_category.strip() if raw_category else None

            # Parsear palabras clave (separadas por coma o punto y coma)
            keywords = []
            if raw_keywords:
                keywords = [k.strip() for k in re.split(r'[;,]', raw_keywords) if k.strip()]

            # Realizar Upsert
            if barcode in existing_map:
                existing_item = existing_map[barcode]
                existing_item.nombre = nombre
                existing_item.precio = price_val
                existing_item.categoria_fse = categoria_fse
                existing_item.palabras_clave_ocr = keywords
                updated_count += 1
            else:
                new_item = PSModel(
                    supermercado_id=supermercado_id,
                    codigo_barras=barcode,
                    nombre=nombre,
                    precio=price_val,
                    categoria_fse=categoria_fse,
                    palabras_clave_ocr=keywords
                )
                new_objects.append(new_item)
                created_count += 1

        # Inserción de lote masivo
        if new_objects:
            db.bulk_save_objects(new_objects)
        
        db.commit()

        return {
            "status": "success",
            "message": f"Carga masiva completada con éxito para '{supermercado_id}'.",
            "creados": created_count,
            "actualizados": updated_count,
            "total_procesado": created_count + updated_count,
            "advertencias": warnings
        }

    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"error": f"Error crítico al procesar la carga batch: {str(e)}"}
        )

# ── Ticket upload & OCR Validation ──────────────────────────────────────────

def get_gemini_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def ocr_ticket_via_gemini(image_path: Path, cart_items: list, supermarket: str) -> tuple[dict | None, str | None]:
    """Uses Gemini 1.5/2.0/2.5 Flash to extract items and total. Returns (data, error_message)."""
    key = get_gemini_key()
    if not key:
        return None, "No se detectó GEMINI_API_KEY ni GOOGLE_API_KEY en variables de entorno."
        
    try:
        genai.configure(api_key=key)
        img = Image.open(image_path)
        
        # Build cart items context to help the model match abbreviations
        import re
        cart_context = ""
        if cart_items:
            cart_context = "Lista de productos esperados (en el carrito virtual del usuario):\n"
            for item in cart_items:
                name = item.get("clean_name") or item.get("name") or ""
                # Strip leading non-alphanumeric (like emojis)
                name_clean = re.sub(r'^[^\w\s]+', '', name).strip()
                cart_context += f"- {name_clean} (€{item.get('price', 0.0)})\n"
        
        prompt = (
            f"Analiza esta imagen de un ticket/preticket de compra del supermercado '{supermarket}'.\n"
            "Extrae el precio total de la compra y la lista de artículos con sus nombres crudos tal y como aparecen en el ticket físico "
            "(ej. con sus abreviaciones de ticket como 'LECH SEMI HAC' o 'LLEN CUI ESSEN') y sus precios individuales.\n"
            "Dado que las líneas de ticket suelen estar muy abreviadas, utiliza la siguiente lista de productos esperados en el carrito del usuario como contexto "
            "para interpretar correctamente lo que pone en el ticket físico:\n\n"
            f"{cart_context}\n"
            "Devuelve EXCLUSIVAMENTE un objeto JSON válido con el siguiente formato, "
            "sin usar bloques de código de markdown (no agregues ```json ni ```, solo el JSON plano):\n"
            '{"total": 12.45, "items": [{"name": "Nombre crudo del ticket", "price": 0.89}, ...]}\n'
            "Si la imagen no es legible o no es un ticket, responde con:\n"
            '{"total": 0.0, "items": []}'
        )
        
        # Try multiple active model versions to support deprecation states in 2026
        model_names = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        response = None
        last_error = None
        
        for name in model_names:
            try:
                print(f"[OCR] Attempting generation with model: {name}")
                model = genai.GenerativeModel(name)
                response = model.generate_content([prompt, img])
                print(f"[OCR] Success with model: {name}")
                break
            except Exception as e:
                last_error = e
                print(f"[OCR] Model {name} failed: {e}")
                
        if response is None:
            raise last_error or Exception("No generative models succeeded.")
            
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
        return data, None
    except Exception as e:
        error_msg = str(e)
        print(f"[OCR] Error calling Gemini: {error_msg}")
        return None, error_msg

@app.post("/upload-ticket")
async def upload_ticket(
    request: Request,
    ticket: UploadFile = File(...),
    cart_total: float = Form(...),
    cart_items: str = Form(...),
    supermarket: str = Form(...),
    db: Session = Depends(get_db)
):
    # Obtener el beneficiario activo
    beneficiary_token = request.cookies.get("beneficiary_token")
    user = None
    if beneficiary_token:
        user = db.query(UserModel).filter(UserModel.token_anonimo == beneficiary_token.strip()).first()
    
    if not user:
        return JSONResponse(
            status_code=403,
            content={"error": "Acceso denegado. Beneficiario no autenticado."}
        )

    # Validar que no supere el saldo disponible en base de datos
    if float(user.saldo_disponible) < cart_total:
        return JSONResponse(
            status_code=400,
            content={"error": f"Saldo insuficiente. Saldo disponible: €{float(user.saldo_disponible):.2f}"}
        )

    file_path = UPLOAD_DIR / ticket.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(ticket.file, buffer)

    # 1. Parse virtual cart items
    try:
        parsed_cart_items = json.loads(cart_items)
    except json.JSONDecodeError:
        parsed_cart_items = []

    # 2. Try Gemini OCR
    ticket_data, debug_error = ocr_ticket_via_gemini(file_path, parsed_cart_items, supermarket)
    
    using_fallback = False
    if not ticket_data:
        using_fallback = True
        print(f"[OCR] Falling back to simulation. Reason: {debug_error}")
        ticket_data = {
            "total": cart_total,
            "items": [{"name": item["name"], "price": item["price"]} for item in parsed_cart_items]
        }

    # 3. Validate (pass get_ticket_mapping and supermarket)
    validator = TicketValidator()
    report = validator.validate(
        cart_items=parsed_cart_items,
        cart_total=cart_total,
        ticket_data=ticket_data,
        get_mapping_func=get_ticket_mapping,
        supermarket=supermarket
    )
    report["using_fallback"] = using_fallback
    report["debug_error"] = debug_error

    # 4. Save learned mappings if validated successfully
    if report["status"] == "validated":
        for mapping in report.get("learned_mappings", []):
            save_ticket_mapping(
                supermarket=mapping["supermarket"],
                raw_ticket_name=mapping["raw_name"],
                barcode=mapping["barcode"]
            )

        # Descontar del saldo del beneficiario en la base de datos
        from decimal import Decimal
        user.saldo_disponible -= Decimal(str(cart_total))
        
        # Registrar en la pista inmutable de auditoría (SQLAlchemy)
        new_audit = ATModel(
            usuario_uuid=user.id,
            supermercado_id=supermarket,
            total=Decimal(str(cart_total)),
            estado="APPROVED"
        )
        db.add(new_audit)
        db.commit()
        db.refresh(new_audit)

        audit_record = {
            "transaction_id": str(new_audit.id),
            "user_id": user.token_anonimo,
            "supermarket": supermarket,
            "timestamp": new_audit.timestamp.isoformat(),
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

@app.get("/api/admin/beneficiaries")
async def list_beneficiaries(
    request: Request,
    db: Session = Depends(get_db)
):
    # Proteger con autenticación de admin y MFA
    admin = await get_current_admin(request, db)
    if not admin:
        return JSONResponse(status_code=403, content={"error": "Acceso no autorizado."})
        
    beneficiaries = db.query(UserModel).filter(UserModel.rol == "beneficiario").all()
    return {
        "beneficiaries": [
            {
                "id": str(b.id),
                "token_anonimo": b.token_anonimo,
                "saldo_disponible": float(b.saldo_disponible)
            } for b in beneficiaries
        ]
    }

from fastapi.responses import RedirectResponse
import urllib.parse

@app.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    # Si ya tiene sesión autenticada y verificada por MFA, redirigir directo al dashboard
    session_token = request.cookies.get("session_token")
    if session_token and session_token in ADMIN_SESSIONS:
        sess = ADMIN_SESSIONS[session_token]
        if sess["mfa_verified"] and datetime.now() < sess["expires"]:
            return RedirectResponse(url="/admin/dashboard", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "state": "login", "error": error}
    )

@app.post("/admin/login")
async def process_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        # Buscar el usuario admin
        user = db.query(UserModel).filter(UserModel.email == email.strip()).first()
        if not user or user.rol != "admin":
            return RedirectResponse(
                url="/admin/login?error=" + urllib.parse.quote("Credenciales incorrectas o acceso no autorizado."),
                status_code=303
            )
        
        # Validar contraseña hashed mediante PBKDF2
        if not verify_password(password, user.hashed_password):
            return RedirectResponse(
                url="/admin/login?error=" + urllib.parse.quote("Contraseña incorrecta."),
                status_code=303
            )
            
        # Crear sesión temporal (mfa_verified = False)
        session_token = secrets.token_hex(32)
        ADMIN_SESSIONS[session_token] = {
            "user_id": user.id,
            "mfa_verified": False,
            "expires": datetime.now() + timedelta(minutes=5)
        }
        
        # Redirigir a setup si el QR no ha sido escaneado aún, de lo contrario a verificación
        next_url = "/admin/setup-mfa" if not user.mfa_enabled else "/admin/verify-mfa"
        response = RedirectResponse(url=next_url, status_code=303)
        response.set_cookie(key="session_token", value=session_token, httponly=True, secure=False)
        return response
    except Exception as e:
        print(f"[AUTH ERROR] Error in process_login: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303
        )

@app.get("/admin/setup-mfa", response_class=HTMLResponse)
async def setup_mfa_page(request: Request, error: Optional[str] = None, db: Session = Depends(get_db)):
    session_token = request.cookies.get("session_token")
    if not session_token or session_token not in ADMIN_SESSIONS:
        return RedirectResponse(url="/admin/login", status_code=303)
        
    sess = ADMIN_SESSIONS[session_token]
    user = db.query(UserModel).filter(UserModel.id == sess["user_id"]).first()
    if not user:
        return RedirectResponse(url="/admin/login", status_code=303)
        
    if user.mfa_enabled:
        return RedirectResponse(url="/admin/verify-mfa", status_code=303)
        
    # Generar URI de provisión TOTP para Authy/Google Authenticator
    totp = pyotp.TOTP(user.mfa_secret)
    provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name="SocialPay")
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(provisioning_uri)}"
    
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "state": "setup",
            "qr_url": qr_url,
            "secret_key": user.mfa_secret,
            "error": error
        }
    )

@app.post("/admin/setup-mfa")
async def process_setup_mfa(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        session_token = request.cookies.get("session_token")
        if not session_token or session_token not in ADMIN_SESSIONS:
            return RedirectResponse(url="/admin/login", status_code=303)
            
        sess = ADMIN_SESSIONS[session_token]
        user = db.query(UserModel).filter(UserModel.id == sess["user_id"]).first()
        if not user or user.mfa_enabled:
            return RedirectResponse(url="/admin/login", status_code=303)
            
        # Validar código TOTP de 6 dígitos ingresado por el usuario
        totp = pyotp.TOTP(user.mfa_secret)
        if totp.verify(code.strip()):
            user.mfa_enabled = True
            db.commit()
            
            # Validar la sesión
            sess["mfa_verified"] = True
            sess["expires"] = datetime.now() + timedelta(hours=2)
            return RedirectResponse(url="/admin/dashboard", status_code=303)
        else:
            return RedirectResponse(
                url="/admin/setup-mfa?error=" + urllib.parse.quote("Código MFA inválido. Reintenta."),
                status_code=303
            )
    except Exception as e:
        print(f"[AUTH ERROR] Error in process_setup_mfa: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303
        )

@app.get("/admin/verify-mfa", response_class=HTMLResponse)
async def verify_mfa_page(request: Request, error: Optional[str] = None):
    session_token = request.cookies.get("session_token")
    if not session_token or session_token not in ADMIN_SESSIONS:
        return RedirectResponse(url="/admin/login", status_code=303)
        
    sess = ADMIN_SESSIONS[session_token]
    if sess["mfa_verified"]:
        return RedirectResponse(url="/admin/dashboard", status_code=303)
        
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "state": "verify", "error": error}
    )

@app.post("/admin/verify-mfa")
async def process_verify_mfa(
    request: Request,
    code: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        session_token = request.cookies.get("session_token")
        if not session_token or session_token not in ADMIN_SESSIONS:
            return RedirectResponse(url="/admin/login", status_code=303)
            
        sess = ADMIN_SESSIONS[session_token]
        user = db.query(UserModel).filter(UserModel.id == sess["user_id"]).first()
        if not user:
            return RedirectResponse(url="/admin/login", status_code=303)
            
        totp = pyotp.TOTP(user.mfa_secret)
        if totp.verify(code.strip()):
            sess["mfa_verified"] = True
            sess["expires"] = datetime.now() + timedelta(hours=2)
            return RedirectResponse(url="/admin/dashboard", status_code=303)
        else:
            return RedirectResponse(
                url="/admin/verify-mfa?error=" + urllib.parse.quote("Código MFA incorrecto."),
                status_code=303
            )
    except Exception as e:
        print(f"[AUTH ERROR] Error in process_verify_mfa: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url="/admin/login?error=" + urllib.parse.quote(f"Error interno del servidor: {str(e)}"),
            status_code=303
        )

@app.get("/admin/logout")
async def logout(request: Request):
    session_token = request.cookies.get("session_token")
    if session_token:
        ADMIN_SESSIONS.pop(session_token, None)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("session_token")
    return response

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    # Protección de sesión de administrador y MFA
    admin = await get_current_admin(request, db)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)
        
    html_path = BASE_DIR / "templates" / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
