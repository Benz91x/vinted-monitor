import os, json, requests, cloudscraper, logging
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
VINTED_BASE_URL  = "https://www.vinted.it"
PRICE_MAX        = 60
SEEN_IDS_FILE    = "seen_ids.json"

# Scarta annunci pubblicati da più di MAX_AGE_DAYS giorni
MAX_AGE_DAYS = 3

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
    "carta", "carte", "card", "cards", "pokemon", "pokémon",
    "yugioh", "yu-gi-oh", "magic the gathering", "mtg",
    "amiibo", "funko",
    "cover", "custodia", "borsa", "zaino", "poster", "tazza",
    "felpa", "maglietta", "t-shirt",
    "umd film", "umd movie",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def get_item_date(item):
    """Restituisce datetime UTC dell'annuncio oppure None."""
    ts = item.get("created_at_ts") or item.get("updated_at_ts")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            pass
    iso = item.get("created_at") or item.get("updated_at")
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
    """
    Salva solo gli ID di annunci recenti (entro MAX_AGE_DAYS).
    Gli ID di annunci più vecchi vengono rimossi automaticamente.
    Questo evita che il file cresca all'infinito e che annunci
    vecchi blocchino quelli nuovi.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    # Tieni solo gli ID che corrispondono ad annunci recenti
    ids_to_keep = set()
    for item_id in seen_ids:
        item = all_items_map.get(item_id)
        if item:
            dt = get_item_date(item)
            if dt and dt >= cutoff:
                ids_to_keep.add(item_id)
            elif not dt:
                # data sconosciuta: teniamo per sicurezza
                ids_to_keep.add(item_id)
        # ID non presente nell'ultimo fetch = annuncio probabilmente rimosso, lo droppiamo

    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids_to_keep)[-2000:], f)
    log.info(f"seen_ids salvati: {len(ids_to_keep)} (rimossi {len(seen_ids) - len(ids_to_keep)} vecchi)")


def is_recent(item):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    dt = get_item_date(item)
    if dt and dt < cutoff:
        log.info(f"  [SKIP troppo vecchio: {dt.date()}] {item.get('title')}")
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
        log.info(f"[{query}] status HTTP: {r.status_code}")
        r.raise_for_status()
        items = r.json().get("items", [])
        log.info(f"[{query}] ricevuti: {len(items)}")
        return items
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


def get_photo_url(item):
    photos = item.get("photos")
    if photos and isinstance(photos, list) and len(photos) > 0:
        p = photos[0]
    else:
        p = item.get("photo") or item.get("image") or {}
    if not p:
        return None
    for field in ("full_size_url", "url", "thumb_url"):
        url = p.get(field)
        if url:
            return url
    return None


def send_photo_item(item):
    amount, currency = get_price(item)
    title  = item.get("title", "N/D")
    url    = item_url(item)
    photo  = get_photo_url(item)

    dt = get_item_date(item)
    data_str = f"\n📅 Pubblicato: {dt.strftime('%d/%m/%Y %H:%M')}" if dt else ""

    caption = (
        f"🎮 *{title}*\n"
        f"💶 *{amount} {currency}*"
        f"{data_str}\n"
        f"🔗 [Vedi su Vinted]({url})"
    )

    if photo:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={
                "chat_id":    TELEGRAM_CHAT_ID,
                "photo":      photo,
                "caption":    caption,
                "parse_mode": "Markdown",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return
        log.warning(f"sendPhoto fallito ({resp.status_code}), fallback testo")

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id":                  TELEGRAM_CHAT_ID,
            "text":                     caption,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )


def send_header(count):
    text = f"🎮 *{count} nuov{'o' if count == 1 else 'i'} annunci PSP su Vinted!*"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )


MAX_NOTIFICATIONS = 10


def main():
    log.info(f"=== Avvio — {datetime.now().strftime('%H:%M:%S')} ===")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    })

    log.info("Step 1: homepage warm-up...")
    scraper.get(VINTED_BASE_URL, timeout=20)

    log.info("Step 2: catalogo warm-up...")
    scraper.get(f"{VINTED_BASE_URL}/catalog",
                params={"search_text": SEARCH_QUERIES[0],
                        "price_to": PRICE_MAX, "order": "newest_first"},
                timeout=20)

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

        to_notify = new_items[:MAX_NOTIFICATIONS]
        extra     = len(new_items) - len(to_notify)

        send_header(len(new_items))
        for item in to_notify:
            send_photo_item(item)

        if extra > 0:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "text":       f"_...e altri {extra} annunci. Apri Vinted per vederli tutti._",
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )

        log.info(f"Notifiche inviate: {len(to_notify)} (+ {extra} non mostrati)")
    else:
        log.info("Nessun annuncio nuovo pertinente e recente — nessuna notifica.")

    # Autopulizia: rimuove dal seen_ids gli ID di annunci più vecchi di MAX_AGE_DAYS
    save_seen_ids(seen_ids, all_items_map)

    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
