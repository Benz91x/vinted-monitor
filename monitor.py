import os, json, requests, cloudscraper, logging
from datetime import datetime

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
VINTED_BASE_URL  = "https://www.vinted.it"
SEARCH_QUERY     = "PSP"
PRICE_MAX        = 60
SEEN_IDS_FILE    = "seen_ids.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            return set(str(i) for i in json.load(f))
    return set()

def save_seen_ids(seen_ids):
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(seen_ids)[-1000:], f)

def fetch_items(scraper):
    url = f"{VINTED_BASE_URL}/api/v2/catalog/items"
    params = {"search_text": SEARCH_QUERY, "price_to": PRICE_MAX,
              "order": "newest_first", "per_page": 96}
    headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
               "Referer": f"{VINTED_BASE_URL}/catalog?search_text={SEARCH_QUERY}"}
    try:
        r = scraper.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        log.error(f"Fetch error: {e}")
        return []

def notify(item):
    title    = item.get("title", "N/D")
    price    = item.get("price", "N/D")
    currency = item.get("currency", "EUR")
    item_id  = item.get("id", "")
    slug     = item.get("url", "")
    url      = f"{VINTED_BASE_URL}/items/{item_id}-{slug}"
    photo    = item.get("photo", {}).get("full_size_url") or item.get("photo", {}).get("url", "")
    caption  = f"🎮 *Nuovo PSP trovato!*\n\n📦 *{title}*\n💰 *{price} {currency}*\n\n🔗 [Acquista ora!]({url})"

    if photo:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                          data={"chat_id": TELEGRAM_CHAT_ID, "photo": photo,
                                "caption": caption, "parse_mode": "Markdown"}, timeout=15)
        if r.ok:
            log.info(f"✅ Notifica inviata: {title}")
            return
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                  data={"chat_id": TELEGRAM_CHAT_ID, "text": caption,
                        "parse_mode": "Markdown"}, timeout=15)
    log.info(f"✅ Notifica inviata: {title}")

def main():
    log.info(f"=== Avvio ciclo — {datetime.now().strftime('%H:%M:%S')} ===")
    
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    # Step 1: homepage per cookie iniziali
    log.info("Step 1: carico homepage...")
    scraper.get(VINTED_BASE_URL, timeout=20)

    # Step 2: pagina di ricerca — questo è il passo mancante che sblocca la API
    log.info("Step 2: carico pagina ricerca...")
    scraper.get(
        f"{VINTED_BASE_URL}/catalog",
        params={"search_text": SEARCH_QUERY, "price_to": PRICE_MAX, "order": "newest_first"},
        timeout=20
    )

    seen_ids  = load_seen_ids()
    items     = fetch_items(scraper)
    new_items = [i for i in items if str(i.get("id")) not in seen_ids]

    log.info(f"Articoli totali: {len(items)} | Nuovi: {len(new_items)}")
    for item in new_items:
        notify(item)
        seen_ids.add(str(item.get("id")))

    if new_items:
        save_seen_ids(seen_ids)
    log.info("=== Fine ciclo ===")
