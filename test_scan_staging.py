import time
import requests

BASE = "https://socialpay-staging.up.railway.app"
BENEFICIARY_TOKEN = "BENEFICIARIO-DEMO"
SEED_BARCODE = "seed-lac-001"   # "Leche entera Hacendado" del catalogo semilla
REAL_BARCODE  = "5449000000996"  # Coca-Cola — barcode real en OFF

results = []

def check(name, ok, detail=""):
    st = "PASS" if ok else "FAIL"
    results.append((st, name))
    line = f"[{st}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


# ── 1. Esperar hasta que staging esté listo con el nuevo deploy ────────────────
print("Esperando deploy de staging...")
for attempt in range(24):          # máximo ~4 minutos
    try:
        r = requests.get(f"{BASE}/api/search?q=leche", timeout=8)
        if r.status_code == 200:
            print(f"Staging responde (intento {attempt+1})\n")
            break
    except Exception:
        pass
    print(f"  intento {attempt+1}/24 — esperando 10s...")
    time.sleep(10)
else:
    print("BLOQUEADO: staging no responde tras 4 minutos")
    exit(1)


# ── 2. Sesión con cookie de beneficiario ──────────────────────────────────────
s = requests.Session()
s.cookies.set("beneficiary_token", BENEFICIARY_TOKEN,
              domain="socialpay-staging.up.railway.app")


# ── 3. /scan-product con barcode del catálogo semilla ────────────────────────
print("=== scan-product: barcode semilla (sin supermarket) ===")
r = s.post(f"{BASE}/scan-product", data={"barcode": SEED_BARCODE})
print(f"  HTTP {r.status_code} | {r.text[:200]}")
if r.status_code == 200:
    d = r.json()
    check("scan-product devuelve 'name'",    "name"     in d)
    check("scan-product devuelve 'allowed'", "allowed"  in d)
    check("scan-product devuelve 'category'","category" in d, d.get("category"))
    check("scan-product devuelve 'price'",   "price"    in d, str(d.get("price")))
    check("scan-product allowed=True para leche", d.get("allowed") == True)
else:
    check("scan-product responde 200", False, f"HTTP {r.status_code}")


# ── 4. /scan-product con barcode real (OFF) ──────────────────────────────────
print("\n=== scan-product: barcode real Coca-Cola (OFF lookup) ===")
r = s.post(f"{BASE}/scan-product", data={"barcode": REAL_BARCODE})
print(f"  HTTP {r.status_code} | {r.text[:200]}")
if r.status_code == 200:
    d = r.json()
    check("scan-product OFF devuelve 'category'", "category" in d, d.get("category"))
    check("scan-product OFF devuelve 'price'",    "price"    in d)
    check("scan-product Coca-Cola allowed",       d.get("allowed") == True)
else:
    check("scan-product OFF responde 200", False, f"HTTP {r.status_code}")


# ── 5. /scan-product bloqueado: cerveza real ─────────────────────────────────
print("\n=== scan-product: barcode cerveza (debe bloquear) ===")
BEER_BARCODE = "5010477381306"   # Heineken 330ml — categoría en:beers en OFF
r = s.post(f"{BASE}/scan-product", data={"barcode": BEER_BARCODE})
print(f"  HTTP {r.status_code} | {r.text[:200]}")
if r.status_code == 200:
    d = r.json()
    check("scan-product cerveza allowed=False", d.get("allowed") == False,
          f"allowed={d.get('allowed')}, category={d.get('category')}")
else:
    check("scan-product cerveza responde 200", False, f"HTTP {r.status_code}")


# ── 6. /scan/manual — producto permitido ─────────────────────────────────────
print("\n=== scan/manual: 'Leche entera' (debe permitirse) ===")
r = s.post(f"{BASE}/scan/manual", data={"product_name": "Leche entera", "price": "1.25"})
print(f"  HTTP {r.status_code} | {r.text[:200]}")
check("scan/manual leche HTTP 200", r.status_code == 200)
if r.status_code == 200:
    d = r.json()
    check("scan/manual devuelve 'verified'", "verified" in d, str(d.get("verified")))
    check("scan/manual leche status=success", d.get("status") == "success")


# ── 7. /scan/manual — producto no catalogado ─────────────────────────────────
print("\n=== scan/manual: 'Producto inventado xyz' (no en BD) ===")
r = s.post(f"{BASE}/scan/manual",
           data={"product_name": "Producto inventado xyz123", "price": "5.00"})
print(f"  HTTP {r.status_code} | {r.text[:200]}")
check("scan/manual desconocido HTTP 200", r.status_code == 200)
if r.status_code == 200:
    d = r.json()
    check("scan/manual desconocido verified=False", d.get("verified") == False,
          f"verified={d.get('verified')}")


# ── 8. /scan/manual sin auth — debería... ────────────────────────────────────
# scan/manual no requiere auth en la implementacion actual (es form simple)
# Verificamos que el endpoint existe y responde
print("\n=== scan/manual: endpoint existe ===")
r2 = requests.post(f"{BASE}/scan/manual",
                   data={"product_name": "Test", "price": "1.00"})
check("scan/manual existe (no 404)", r2.status_code != 404, f"HTTP {r2.status_code}")


# ── Resumen ───────────────────────────────────────────────────────────────────
passed = sum(1 for s, _ in results if s == "PASS")
failed = sum(1 for s, _ in results if s == "FAIL")
print(f"\n{'='*55}")
print(f"RESULTADO: {passed} PASS / {failed} FAIL / {len(results)} total")
if failed:
    print("FALLOS:")
    for st, name in results:
        if st == "FAIL":
            print(f"  - {name}")
