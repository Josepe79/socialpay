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
