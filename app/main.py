import secrets

import pyotp
from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from app.config import SEED_CATALOG
from app.database import SessionLocal, engine
from app.deps import hash_password
from app.models import Base, Producto, Usuario as UserModel
from app.routers import admin, auth, beneficiario, gestor, supermercado, upspain
from app.security import limiter

app = FastAPI(
    title="Plataforma de Validación y Auditoría de Ayudas Sociales FSE+",
    description=(
        "API gubernamental y corporativa para la gestión segregada de roles, "
        "carga batch de catálogos de supermercados, asignación de fondos FSE+ y "
        "validación automatizada de tickets mediante OCR, con cumplimiento RGPD."
    ),
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://world.openfoodfacts.org https://world.openfoodfacts.net;"
    )
    return response


app.include_router(auth.router)
app.include_router(beneficiario.router)
app.include_router(admin.router)
app.include_router(supermercado.router)
app.include_router(upspain.router)
app.include_router(gestor.router)


def _init_db():
    try:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        has_users = "usuarios" in inspector.get_table_names()
        has_alloc = "asignacion_fondos_gestor" in inspector.get_table_names()

        if has_users:
            cols = [c["name"] for c in inspector.get_columns("usuarios")]
            required = [
                "email", "hashed_password", "mfa_secret", "mfa_enabled",
                "gestor_uuid", "codigo_proyecto_fse", "creado_por",
                "nombre_institucion", "cif", "direccion", "responsable", "movil_mfa",
            ]
            missing = [c for c in required if c not in cols]
            if missing:
                print(f"[DB] Columnas faltantes en 'usuarios': {missing}. Ejecuta Alembic.")

        if not has_alloc:
            print("[DB] Tabla 'asignacion_fondos_gestor' no existe.")

        print("[DB] Inicializando tablas SQLAlchemy...")
        Base.metadata.create_all(bind=engine)
        print("[DB] Tablas SQLAlchemy inicializadas.")
    except Exception as e:
        print(f"[DB] Error creando tablas: {e}")

    try:
        db: Session = SessionLocal()
        admin_user = db.query(UserModel).filter(UserModel.email == "admin@jepco.es").first()
        if not admin_user:
            print("[DB] Creando usuario administrador semilla: admin@jepco.es...")
            default_password = "JepcoAdmin2026!"
            mfa_secret_key = pyotp.random_base32()
            db.add(UserModel(
                token_anonimo=f"ADMIN-TOKEN-{secrets.token_hex(4).upper()}",
                saldo_disponible=0.00,
                rol="admin",
                email="admin@jepco.es",
                hashed_password=hash_password(default_password),
                mfa_secret=mfa_secret_key,
                mfa_enabled=False,
            ))
            db.commit()
            print(f"[DB] Admin semilla creado. Contraseña: {default_password}")
            print(f"[DB] Clave TOTP: {mfa_secret_key}")
        else:
            print("[DB] El administrador admin@jepco.es ya existe.")

        if not db.query(UserModel).filter(UserModel.token_anonimo == "BENEFICIARIO-DEMO").first():
            print("[DB] Creando beneficiario semilla: BENEFICIARIO-DEMO...")
            db.add(UserModel(
                token_anonimo="BENEFICIARIO-DEMO",
                saldo_disponible=150.00,
                rol="beneficiario",
            ))
            db.commit()
            print("[DB] Beneficiario semilla creado.")
        else:
            print("[DB] El beneficiario BENEFICIARIO-DEMO ya existe.")
        db.close()
    except Exception as e:
        print(f"[DB] Error al sembrar usuarios: {e}")

    try:
        db2: Session = SessionLocal()
        count = db2.query(Producto).count()
        if count == 0:
            print(f"[DB] Cargando {len(SEED_CATALOG)} productos semilla...")
            db2.bulk_save_objects([
                Producto(barcode=b, name=n, category=c, source="local")
                for b, n, c in SEED_CATALOG
            ])
            db2.commit()
            print("[DB] Catálogo semilla cargado.")
        else:
            print(f"[DB] BBDD ya tiene {count} productos.")
        db2.close()
    except Exception as e:
        print(f"[DB] Error cargando catálogo semilla: {e}")


@app.on_event("startup")
def on_startup():
    _init_db()
