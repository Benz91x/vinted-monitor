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
    params = {
        "search_text":  SEARCH_QUERY,
        "price_to":     PRICE_MAX,
        "order":        "newest_first",
        "per_page":     96,
        "status_ids[]": 1,          # solo articoli DISPONIBILI (non venduti)
    }
    headers = {
        "Accept":              "application/json, text/plain, */*",
        "X-Requested-With":    "XMLHttpRequest",
        "Referer":             f"{VINTED_BASE_URL}/catalog?search_text={SEARCH_QUERY}",
    }
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


def get_price(item):
    """Estrae prezzo e valuta in modo robusto (il campo può essere dict o stringa)."""
    price = item.get("price", {})
    if isinstance(price, dict):
        return price.get("amount", "N/D"), price.get("currency_code", "EUR")
    # fallback: stringa diretta
    return str(price), item.get("currency", "EUR")


def item_url(item):
    """URL diretto all'articolo — solo ID, senza slug (evita 404).
       Su mobile con app Vinted installata si apre direttamente nell'app."""
    return f"{VINTED_BASE_URL}/items/{item.get('id', '')}"


def send_telegram(text):
    """Invia un messaggio di testo con Markdown."""
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     text,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )


def build_summary(new_items):
    """
    Costruisce UN SOLO messaggio riepilogativo con tutti i nuovi articoli.
    Telegram ha un limite di 4096 caratteri — se ci sono molti articoli
    li tronca a 15 e aggiunge quanti ne mancano.
    """
    count = len(new_items)
    lines = [f"🎮 *{count} nuov{'o' if count == 1 else 'i'} annunci PSP su Vinted!*\n"]

    shown   = new_items[:15]   # mostra max 15 per non sforare il limite Telegram
    hidden  = count - len(shown)

    for item in shown:
        amount, currency = get_price(item)
        title = item.get("title", "N/D")
        url   = item_url(item)
        lines.append(f"• [{title}]({url}) — *{amount} {currency}*")

    if hidden > 0:
        lines.append(f"\n_...e altri {hidden} annunci. Apri Vinted per vederli tutti._")

    return "\n".join(lines)


def main():
    log.info(f"=== Avvio — {datetime.now().strftime('%H:%M:%S')} ===")

    # Sessione con doppio warm-up per bypassare Cloudflare
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    log.info("Step 1: homepage...")
    scraper.get(VINTED_BASE_URL, timeout=20)

    log.info("Step 2: catalogo...")
    scraper.get(f"{VINTED_BASE_URL}/catalog",
                params={"search_text": SEARCH_QUERY,
                        "price_to": PRICE_MAX, "order": "newest_first"},
                timeout=20)

    seen_ids  = load_seen_ids()
    log.info(f"ID già visti: {len(seen_ids)}")

    items     = fetch_items(scraper)
    new_items = [i for i in items if str(i.get("id")) not in seen_ids]
    log.info(f"Nuovi annunci: {len(new_items)}")

    if new_items:
        # Aggiorna seen_ids PRIMA di notificare (evita doppi se Telegram è lento)
        for item in new_items:
            seen_ids.add(str(item.get("id")))
        save_seen_ids(seen_ids)

        # UNA SOLA notifica riepilogativa
        summary = build_summary(new_items)
        send_telegram(summary)
        log.info(f"Notifica inviata: {len(new_items)} articoli")
    else:
        log.info("Nessun articolo nuovo — nessuna notifica inviata.")

    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
