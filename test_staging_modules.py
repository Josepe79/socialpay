import requests

BASE = "https://socialpay-staging.up.railway.app"
s = requests.Session()

results = []

def check(name, resp, expected_status, check_body=None):
    ok = resp.status_code == expected_status
    if ok and check_body:
        ok = check_body in resp.text
    status = "PASS" if ok else "FAIL"
    results.append((status, name, resp.status_code))
    print(f"[{status}] {name} -> HTTP {resp.status_code}" + (f" (esperado {expected_status})" if not ok else ""))

# -- auth router --
r = s.get(f"{BASE}/admin/login", allow_redirects=True)
check("GET /admin/login - carga pagina login", r, 200, "csrf_token")

r = s.get(f"{BASE}/admin/logout", allow_redirects=False)
check("GET /admin/logout - redirige a login", r, 303)

r = s.get(f"{BASE}/admin/setup-mfa", allow_redirects=False)
check("GET /admin/setup-mfa - redirige si no hay sesion", r, 303)

r = s.get(f"{BASE}/admin/verify-mfa", allow_redirects=False)
check("GET /admin/verify-mfa - redirige si no hay sesion", r, 303)

# -- beneficiario router --
r = s.get(f"{BASE}/", allow_redirects=True)
check("GET / - carga login beneficiario", r, 200)

r = s.get(f"{BASE}/beneficiario/logout", allow_redirects=False)
check("GET /beneficiario/logout - redirige a /", r, 303)

r = s.get(f"{BASE}/api/search?q=leche", allow_redirects=True)
check("GET /api/search?q=leche - busqueda funciona", r, 200)
if r.status_code == 200:
    data = r.json()
    has_key = "products" in data
    results.append(("PASS" if has_key else "FAIL", "/api/search devuelve {products}", r.status_code))
    print(f"  -> JSON contiene 'products': {'PASS' if has_key else 'FAIL'} | {len(data.get('products', []))} resultados")

# -- admin router --
r = s.get(f"{BASE}/admin/dashboard", allow_redirects=False)
check("GET /admin/dashboard - redirige a login (no autenticado)", r, 303)

r = s.get(f"{BASE}/api/admin/products", allow_redirects=True)
check("GET /api/admin/products - 403 sin sesion", r, 403)

r = s.get(f"{BASE}/api/admin/audit-logs", allow_redirects=True)
check("GET /api/admin/audit-logs - 403 sin sesion", r, 403)

r = s.get(f"{BASE}/api/admin/beneficiaries", allow_redirects=True)
check("GET /api/admin/beneficiaries - 403 sin sesion", r, 403)

r = s.get(f"{BASE}/api/admin/system-users", allow_redirects=True)
check("GET /api/admin/system-users - 403 sin sesion", r, 403)

# -- supermercado router --
r = s.get(f"{BASE}/supermercado/dashboard", allow_redirects=False)
check("GET /supermercado/dashboard - redirige a login", r, 303)

r = s.get(f"{BASE}/api/supermercado/dashboard-data", allow_redirects=True)
check("GET /api/supermercado/dashboard-data - 403 sin sesion", r, 403)

# -- upspain router --
r = s.get(f"{BASE}/upspain/dashboard", allow_redirects=False)
check("GET /upspain/dashboard - redirige a login", r, 303)

r = s.get(f"{BASE}/api/upspain/dashboard-data", allow_redirects=True)
check("GET /api/upspain/dashboard-data - 403 sin sesion", r, 403)

# -- gestor router --
r = s.get(f"{BASE}/gestor/dashboard", allow_redirects=False)
check("GET /gestor/dashboard - redirige a login", r, 303)

r = s.get(f"{BASE}/api/gestor/dashboard-data", allow_redirects=True)
check("GET /api/gestor/dashboard-data - 403 sin sesion", r, 403)

# -- security headers --
r = s.get(f"{BASE}/admin/login", allow_redirects=True)
headers = r.headers
sec_checks = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Strict-Transport-Security", "max-age="),
    ("Content-Security-Policy", "default-src"),
    ("Referrer-Policy", "strict-origin"),
    ("Permissions-Policy", "camera=()"),
]
print("\n-- Security Headers --")
for hdr, val in sec_checks:
    present = val in headers.get(hdr, "")
    status = "PASS" if present else "FAIL"
    results.append((status, f"Header {hdr}", 0))
    print(f"[{status}] {hdr}: {headers.get(hdr, '(ausente)')}")

# -- docs endpoint (verifica que FastAPI registra todos los routers) --
r = s.get(f"{BASE}/openapi.json")
if r.status_code == 200:
    paths = list(r.json().get("paths", {}).keys())
    expected = ["/admin/login", "/", "/api/search", "/api/admin/products",
                "/api/supermercado/dashboard-data", "/api/upspain/dashboard-data",
                "/api/gestor/dashboard-data"]
    print("\n-- Rutas registradas en OpenAPI --")
    for ep in expected:
        found = ep in paths
        status = "PASS" if found else "FAIL"
        results.append((status, f"Ruta {ep} en OpenAPI", 0))
        print(f"[{status}] {ep}")

# Summary
passed = sum(1 for s, *_ in results if s == "PASS")
failed = sum(1 for s, *_ in results if s == "FAIL")
print(f"\n{'='*50}")
print(f"RESULTADO: {passed} PASS / {failed} FAIL / {len(results)} total")
if failed == 0:
    print("Todos los modulos responden correctamente.")
else:
    print("FALLOS detectados:")
    for st, name, code in results:
        if st == "FAIL":
            print(f"  - {name} (HTTP {code})")
