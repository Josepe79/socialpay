import logging
import os
import unicodedata
from pathlib import Path

import google.generativeai as genai
from fastapi.templating import Jinja2Templates
from PIL import Image

from logic.matcher import ProductMatcher

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
matcher = ProductMatcher()

security_logger = logging.getLogger("security")
security_logger.setLevel(logging.WARNING)
if not security_logger.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("[SECURITY LOG] %(asctime)s - %(message)s"))
    security_logger.addHandler(_sh)


def normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def parse_spanish_float(val) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.strip().replace("€", "").replace(" ", "")
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        return float(cleaned)
    return float(val)


def get_gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def ocr_ticket_via_gemini(image_path: Path, cart_items: list, supermarket: str) -> tuple:
    """Uses Gemini Flash to extract items and total. Returns (data, error_message)."""
    import json, re
    key = get_gemini_key()
    if not key:
        return None, "No se detectó GEMINI_API_KEY ni GOOGLE_API_KEY en variables de entorno."
    try:
        genai.configure(api_key=key)
        img = Image.open(image_path)

        cart_context = ""
        if cart_items:
            cart_context = "Lista de productos esperados (en el carrito virtual del usuario):\n"
            for item in cart_items:
                name = item.get("clean_name") or item.get("name") or ""
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
