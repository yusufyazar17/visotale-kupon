"""
Visotale kupon servisi.

POST /kupon  {"email": "...", "consent": true}
  → {"code": "AB12CD", "expires_at": 1763000000, "percent": 10}

Kural: kupon yalnızca e-posta izni verilirse üretilir. Aynı e-posta 24 saat
içinde tekrar isterse aynı kod döner (yeni kod üretilmez).

Railway Variables:
  SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET  (visotale-backend ile aynı)
  SHOPIFY_API_VERSION   (opsiyonel, backend'dekiyle aynı)
  ALLOWED_ORIGINS       (opsiyonel, varsayılan https://visotale.com,https://www.visotale.com)
  KUPON_YUZDE=10, KUPON_SAAT=24  (opsiyonel)
"""

import os
import re
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import shopify_admin

ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://visotale.com,https://www.visotale.com").split(",") if o.strip()]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["POST", "OPTIONS"],
                   allow_headers=["Content-Type"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ISSUED: dict = {}                      # email -> {"code", "expires_at"}
HITS: dict = defaultdict(deque)        # ip -> zaman damgaları
MAX_PER_HOUR = 5


def _limited(ip: str) -> bool:
    q = HITS[ip]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= MAX_PER_HOUR:
        return True
    q.append(now)
    return False


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/kupon")
async def kupon(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Geçersiz istek."}, status_code=400)

    email = str(body.get("email", "")).strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 200:
        return JSONResponse({"error": "Geçerli bir e-posta adresi gir."}, status_code=400)
    if body.get("consent") is not True:
        return JSONResponse({"error": "İndirim için e-posta iznini onaylaman gerekiyor."}, status_code=400)

    prev = ISSUED.get(email)
    if prev and prev["expires_at"] > time.time():
        return {**prev, "percent": int(shopify_admin.PERCENT)}

    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    if _limited(ip):
        return JSONResponse({"error": "Çok fazla deneme yapıldı, biraz sonra tekrar dene."}, status_code=429)

    # Önce izni kaydet; kayıt başarısızsa kupon yine verilir ama loga düşer
    try:
        shopify_admin.subscribe(email)
    except Exception as e:  # noqa: BLE001
        print(f"[kupon] izin kaydedilemedi ({email}): {e}", flush=True)

    try:
        d = shopify_admin.create_discount(email)
    except Exception as e:  # noqa: BLE001
        print(f"[kupon] kupon oluşturulamadı ({email}): {e}", flush=True)
        return JSONResponse({"error": "Şu an indirim kodu oluşturulamadı. Siparişine indirimsiz devam edebilirsin."},
                            status_code=502)

    ISSUED[email] = d
    print(f"[kupon] {email} -> {d['code']}", flush=True)
    return {**d, "percent": int(shopify_admin.PERCENT)}
