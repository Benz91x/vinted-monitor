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

def send_telegram(text, photo_url=None):
    if photo_url:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url,
                  "caption": text, "parse_mode": "Markdown"}, timeout=15)
        if r.ok:
            return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text,
              "parse_mode": "Markdown"}, timeout=15)

def fetch_items(scraper):
    params = {"search_text": SEARCH_QUERY, "price_to": PRICE_MAX,
              "order": "newest_first", "per_page": 96,
              "status_ids[]": 1}
    headers = {"Accept": "application/json, text/plain, */*",
               "X-Requested-With": "XMLHttpRequest",
               "Referer": f"{VINTED_BASE_URL}/catalog?search_text={SEARCH_QUERY}"}
    try:
        r = scraper.get(f"{VINTED_BASE_URL}/api/v2/catalog/items",
                        params=params, headers=headers, timeout=20)
        log.info(f"Vinted API status: {r.status_code}")
        r.raise_for_status()
        items = r.json().get("items", [])
        log.info(f"Articoli ricevuti: {len(items)}")
        return items
    except Exception as e:
        log.error(f"Fetch error: {e}")
        return []

def main():
    log.info(f"=== Avvio — {datetime.now().strftime('%H:%M:%S')} ===")
    log.info(f"TOKEN presente: {'SI' if TELEGRAM_TOKEN else 'NO'}")
    log.info(f"CHAT_ID presente: {'SI' if TELEGRAM_CHAT_ID else 'NO'}")

    # Test Telegram subito
    send_telegram("🔄 *Monitor PSP — ciclo avviato*\nSto controllando Vinted...")
    log.info("Messaggio di test Telegram inviato")

    # Sessione con doppio warm-up
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    log.info("Step 1: carico homepage Vinted...")
    r1 = scraper.get(VINTED_BASE_URL, timeout=20)
    log.info(f"Homepage status: {r1.status_code}")

    log.info("Step 2: carico pagina catalogo...")
    r2 = scraper.get(f"{VINTED_BASE_URL}/catalog",
                     params={"search_text": SEARCH_QUERY,
                             "price_to": PRICE_MAX, "order": "newest_first"},
                     timeout=20)
    log.info(f"Catalogo status: {r2.status_code}")

    seen_ids  = load_seen_ids()
    log.info(f"ID gia visti: {len(seen_ids)}")

    items     = fetch_items(scraper)
    new_items = [i for i in items if str(i.get("id")) not in seen_ids]
    log.info(f"Nuovi annunci: {len(new_items)}")

    if new_items:
        for item in new_items:
            title    = item.get("title", "N/D")
            price    = item.get("price", "N/D")
            currency = item.get("currency", "EUR")
            item_id  = item.get("id", "")
            slug     = item.get("url", "")
            url      = f"{VINTED_BASE_URL}/items/{item_id}-{slug}"
            photo    = (item.get("photo") or {}).get("full_size_url") or \
                       (item.get("photo") or {}).get("url", "")
            caption  = (f"🎮 *Nuovo PSP su Vinted!*\n\n"
                        f"📦 *{title}*\n"
                        f"💰 *{price} {currency}*\n\n"
                        f"🔗 [👉 Acquista subito!]({url})")
            send_telegram(caption, photo)
            seen_ids.add(str(item_id))
            log.info(f"Notificato: {title} — {price}€")
        save_seen_ids(seen_ids)
    else:
        if items:
            send_telegram(f"✅ Controllo completato: {len(items)} annunci trovati, nessuno nuovo.")
        else:
            send_telegram("⚠️ Vinted non ha restituito risultati (possibile blocco temporaneo).")

    log.info("=== Fine ciclo ===")

if __name__ == "__main__":
    main()
