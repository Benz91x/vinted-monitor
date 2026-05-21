import os, json, requests, cloudscraper, logging
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
VINTED_BASE_URL  = "https://www.vinted.it"
PRICE_MAX        = 60
SEEN_IDS_FILE    = "seen_ids.json"

# Scarta annunci pubblicati da più di MAX_AGE_HOURS ore
MAX_AGE_HOURS = 24

SEARCH_QUERIES = [
    "PSP",
    "PlayStation Portable",
    "psp 1000",
    "psp 2000",
    "psp 3000",
    "psp go",
]

BLACKLIST_KEYWORDS = [
    "ps4", "ps5", "ps3", "ps2", "playstation 4", "playstation 5",
    "playstation 3", "playstation 2",
    "xbox", "nintendo", "switch", "wii",
    "carta", "carte", "card", "cards", "pokemon", "pok\u00e9mon",
    "yugioh", "yu-gi-oh", "magic the gathering", "mtg",
    "amiibo", "funko",
    "cover", "custodia", "borsa", "zaino", "poster", "tazza",
    "felpa", "maglietta", "t-shirt",
    "umd film", "umd movie",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def get_item_date(item):
    """
    Legge la data di PUBBLICAZIONE dell'annuncio.
    Vinted espone 'created_at_ts' (unix) o 'created_at' (ISO).
    """
    # Preferisce created_at_ts (timestamp creazione)
    for key in ("created_at_ts", "updated_at_ts"):
        ts = item.get(key)
        if ts:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass
    # Fallback stringa ISO
    for key in ("created_at", "updated_at"):
        iso = item.get(key)
        if iso:
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except Exception:
                pass
    return None


def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            return set(str(i) for i in json.load(f))
    return set()


def save_seen_ids(seen_ids, all_items_map):
    """Salva solo ID di annunci recenti (entro MAX_AGE_HOURS). Autopulizia automatica."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    ids_to_keep = set()
    for item_id in seen_ids:
        item = all_items_map.get(item_id)
        if item:
            dt = get_item_date(item)
            if dt is None or dt >= cutoff:
                ids_to_keep.add(item_id)
        # Se l'annuncio non è più nel fetch, probabilmente rimosso: lo droppiamo
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids_to_keep)[-2000:], f)
    log.info(f"seen_ids salvati: {len(ids_to_keep)} (rimossi {len(seen_ids) - len(ids_to_keep)} vecchi)")


def is_recent(item):
    """True se l'annuncio è stato pubblicato nelle ultime MAX_AGE_HOURS ore."""
    dt = get_item_date(item)
    if dt is None:
        return True  # data sconosciuta: lascia passare
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    if dt < cutoff:
        log.info(f"  [SKIP vecchio {dt.strftime('%d/%m %H:%M')}] {item.get('title')}")
        return False
    return True


def is_relevant(item):
    title       = item.get("title", "").lower()
    description = item.get("description", "").lower()
    brand       = (item.get("brand_title") or "").lower()
    text        = f"{title} {description} {brand}"

    for kw in BLACKLIST_KEYWORDS:
        if kw in text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    psp_terms = ["psp", "playstation portable"]
    if not any(t in title for t in psp_terms):
        log.info(f"  [SKIP no-psp-in-title] {item.get('title')}")
        return False

    return True


def fetch_items(scraper, query):
    params = {
        "search_text":  query,
        "price_to":     PRICE_MAX,
        "order":        "newest_first",
        "per_page":     96,
        "status_ids[]": 1,
    }
    headers = {
        "Accept":           "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          f"{VINTED_BASE_URL}/catalog?search_text={query}",
    }
    try:
        r = scraper.get(f"{VINTED_BASE_URL}/api/v2/catalog/items",
                        params=params, headers=headers, timeout=20)
        log.info(f"[{query}] HTTP {r.status_code}, ricevuti: {len(r.json().get('items', []))}")
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        log.error(f"[{query}] Fetch error: {e}")
        return []


def get_price(item):
    price = item.get("price", {})
    if isinstance(price, dict):
        return price.get("amount", "N/D"), price.get("currency_code", "EUR")
    return str(price), item.get("currency", "EUR")


def item_url(item):
    return f"{VINTED_BASE_URL}/items/{item.get('id', '')}"


def send_summary(items):
    """
    Invia UN UNICO messaggio Telegram con tutti gli annunci nuovi.
    Formato per ogni annuncio:
      🎮 Titolo — 💶 Prezzo  📅 Data  🔗 Link
    Telegram supporta max 4096 caratteri per messaggio;
    se supera il limite, manda messaggi aggiuntivi.
    """
    header = f"🎮 *{len(items)} nuov{'o' if len(items)==1 else 'i'} annunci PSP su Vinted!*\n\n"
    lines = []
    for item in items:
        amount, currency = get_price(item)
        title = item.get("title", "N/D")
        url   = item_url(item)
        dt    = get_item_date(item)
        data_str = dt.strftime("%d/%m %H:%M") if dt else "?"
        lines.append(
            f"🎮 [{title}]({url})\n"
            f"💶 *{amount} {currency}*  📅 {data_str}\n"
        )

    # Raggruppa le righe in messaggi da max 4000 char
    MAX_LEN = 4000
    chunks  = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > MAX_LEN:
            chunks.append(current)
            current = line
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current)

    for chunk in chunks:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id":                  TELEGRAM_CHAT_ID,
                "text":                     chunk,
                "parse_mode":               "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )


def main():
    log.info(f"=== Avvio \u2014 {datetime.now().strftime('%H:%M:%S')} ===")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    scraper.get(VINTED_BASE_URL, timeout=20)
    scraper.get(f"{VINTED_BASE_URL}/catalog",
                params={"search_text": SEARCH_QUERIES[0], "price_to": PRICE_MAX,
                        "order": "newest_first"}, timeout=20)

    seen_ids = load_seen_ids()
    log.info(f"ID già visti: {len(seen_ids)}")

    all_items_map = {}
    for query in SEARCH_QUERIES:
        for item in fetch_items(scraper, query):
            item_id = str(item.get("id"))
            if item_id not in all_items_map:
                all_items_map[item_id] = item

    log.info(f"Articoli unici (pre-filtro): {len(all_items_map)}")

    new_items = [
        item for item_id, item in all_items_map.items()
        if item_id not in seen_ids
        and is_recent(item)
        and is_relevant(item)
    ]
    log.info(f"Nuovi annunci pertinenti e recenti: {len(new_items)}")

    if new_items:
        for item in new_items:
            seen_ids.add(str(item.get("id")))
        send_summary(new_items)  # unico messaggio riepilogativo
        log.info(f"Notifica inviata: {len(new_items)} annunci")
    else:
        log.info("Nessun annuncio nuovo — nessuna notifica.")

    save_seen_ids(seen_ids, all_items_map)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
