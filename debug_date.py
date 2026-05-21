"""
Script di debug temporaneo.
Stampa tutti i campi di un annuncio PSP cosi' vediamo quale campo
corrisponde al 'Caricato' visibile su Vinted.
"""
import json, cloudscraper
from datetime import datetime, timezone

VINTED_BASE_URL = "https://www.vinted.it"

scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False})
scraper.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
})

print("Warm-up homepage...")
scraper.get(VINTED_BASE_URL, timeout=20)

print("Fetch annunci PSP...")
r = scraper.get(
    f"{VINTED_BASE_URL}/api/v2/catalog/items",
    params={
        "search_text":  "PSP",
        "price_to":     60,
        "order":        "newest_first",
        "per_page":     5,
        "status_ids[]": 1,
    },
    headers={
        "Accept":           "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    },
    timeout=20,
)

print(f"HTTP status: {r.status_code}")
items = r.json().get("items", [])
print(f"Annunci ricevuti: {len(items)}")

for i, item in enumerate(items[:3]):
    print(f"\n====== ANNUNCIO {i+1}: {item.get('title')} ======")
    print(f"URL: https://www.vinted.it/items/{item.get('id')}")
    print("--- CAMPI DATA (tutti i campi con 'at', 'ts', 'time', 'date', 'ago') ---")
    for k, v in item.items():
        if any(x in k.lower() for x in ["_at", "_ts", "time", "date", "ago", "upload", "pubbl"]):
            # Se e' un timestamp unix, converti in data leggibile
            display = v
            if isinstance(v, (int, float)) and v > 1000000000:
                try:
                    display = f"{v} => {datetime.fromtimestamp(int(v), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                except Exception:
                    pass
            print(f"  {k}: {display}")
    print("--- TUTTI I CAMPI SEMPLICI ---")
    for k, v in item.items():
        if not isinstance(v, (dict, list)):
            print(f"  {k}: {v}")
