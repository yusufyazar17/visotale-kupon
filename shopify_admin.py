"""
Visotale kupon servisi — Shopify Admin API (GraphQL) çağrıları.

1) create_discount(email): tek kullanımlık, %10, 24 saat geçerli indirim kodu
2) subscribe(email): müşteriyi e-posta pazarlama izniyle (SUBSCRIBED) kaydeder

Gerekli uygulama izinleri: write_discounts, write_customers, read_customers
"""

import os
import random
import string
import time

import requests

import shopify_auth

STORE = os.environ.get("SHOPIFY_STORE_DOMAIN", "")
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2024-10")
PERCENT = float(os.environ.get("KUPON_YUZDE", "10"))
VALID_HOURS = float(os.environ.get("KUPON_SAAT", "24"))

# Karışması kolay harfler (0/O, 1/I) çıkarıldı
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _gql(query, variables):
    token = shopify_auth.get_access_token()
    if not token:
        raise RuntimeError("Shopify token alınamadı")
    r = requests.post(
        f"https://{STORE}/admin/api/{API_VERSION}/graphql.json",
        json={"query": query, "variables": variables},
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        timeout=12,
    )
    if not r.ok:
        raise RuntimeError(f"GraphQL HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL hata: {data['errors']}")
    return data["data"]


DISCOUNT_MUTATION = """
mutation($d: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $d) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
"""


def create_discount(email: str) -> dict:
    """{"code": "...", "expires_at": epoch_saniye} döner, hata olursa RuntimeError."""
    code = "".join(random.choices(ALPHABET, k=6))
    now = time.time()
    ends = now + VALID_HOURS * 3600
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    variables = {
        "d": {
            "title": f"Wizard %{int(PERCENT)} — {email}",
            "code": code,
            "startsAt": time.strftime(fmt, time.gmtime(now - 60)),
            "endsAt": time.strftime(fmt, time.gmtime(ends)),
            "customerSelection": {"all": True},
            "customerGets": {"value": {"percentage": PERCENT / 100}, "items": {"all": True}},
            "appliesOncePerCustomer": True,
            "usageLimit": 1,
        }
    }
    res = _gql(DISCOUNT_MUTATION, variables)["discountCodeBasicCreate"]
    if res.get("userErrors"):
        raise RuntimeError(f"İndirim oluşturulamadı: {res['userErrors']}")
    return {"code": code, "expires_at": int(ends)}


CREATE_CUSTOMER = """
mutation($input: CustomerInput!) {
  customerCreate(input: $input) {
    customer { id }
    userErrors { field message }
  }
}
"""

FIND_CUSTOMER = """
query($q: String!) { customers(first: 1, query: $q) { nodes { id } } }
"""

UPDATE_CONSENT = """
mutation($input: CustomerEmailMarketingConsentUpdateInput!) {
  customerEmailMarketingConsentUpdate(input: $input) {
    userErrors { field message }
  }
}
"""


def subscribe(email: str) -> None:
    """Müşteriyi oluşturur ya da bulur, e-posta pazarlama iznini SUBSCRIBED yapar."""
    consent = {
        "marketingState": "SUBSCRIBED",
        "marketingOptInLevel": "SINGLE_OPT_IN",
        "consentUpdatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    res = _gql(CREATE_CUSTOMER, {"input": {
        "email": email,
        "tags": ["wizard-kupon"],
        "emailMarketingConsent": consent,
    }})["customerCreate"]
    if not res.get("userErrors"):
        return
    # Müşteri zaten varsa izni güncelle
    nodes = _gql(FIND_CUSTOMER, {"q": f"email:{email}"})["customers"]["nodes"]
    if not nodes:
        raise RuntimeError(f"Müşteri oluşturulamadı: {res['userErrors']}")
    upd = _gql(UPDATE_CONSENT, {"input": {"customerId": nodes[0]["id"], "emailMarketingConsent": consent}})
    errs = upd["customerEmailMarketingConsentUpdate"].get("userErrors")
    if errs:
        raise RuntimeError(f"İzin güncellenemedi: {errs}")
