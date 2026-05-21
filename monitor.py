import os, json, requests, cloudscraper, logging
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 60
SEEN_IDS_FILE    = "seen_ids.json"
MAX_SEEN_IDS     = 5000
# Finestra massima di età per considerare un annuncio "recente" (in ore)
MAX_ITEM_AGE_HOURS = int(os.environ.get("MAX_ITEM_AGE_HOURS", "24"))

VINTED_DOMAINS = [
    "https://www.vinted.it",
    "https://www.vinted.es",
    "https://www.vinted.fr",
    "https://www.vinted.de",
]

SEARCH_QUERIES = [
    "PSP",
    "PlayStation Portable",
    "psp 1000",
    "psp 2000",
    "psp 3000",
    "psp go",
    "consola psp",
    "console psp",
]

BLACKLIST_KEYWORDS = [
    "ps4", "ps5", "ps3", "ps2",
    "playstation 4", "playstation 5", "playstation 3", "playstation 2",
    "xbox", "nintendo", "switch", "wii",
    "carta", "carte", "card", "cards",
    "pokemon", "pokémon", "yugioh", "yu-gi-oh", "magic the gathering", "mtg",
    "amiibo", "funko",
    "cover", "custodia", "borsa", "zaino", "poster", "tazza",
    "felpa", "maglietta", "t-shirt",
    "umd film", "umd movie",
    "president safety",
    "p.s.p",
]

PSP_TITLE_TERMS = [
    "psp",
    "playstation portable",
    "playstation-portable",
    "ps portable",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def load_seen_ids():
    """Restituisce (seen_ids, is_first_run).
    is_first_run=True se il file non esiste o e' vuoto: in quel caso
    il run corrente serve solo a popolare il baseline, senza notifiche.
    """
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            data = json.load(f)
        if data:  # file esiste ed e' popolato
            return set(str(i) for i in data), False
    return set(), True  # file mancante o vuoto = primo avvio


def save_seen_ids(seen_ids):
    ordered = sorted(seen_ids, key=lambda x: int(x), reverse=True)[:MAX_SEEN_IDS]
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(ordered, f)
    log.info(f"seen_ids salvati: {len(ordered)}")


def is_relevant(item):
    title = (item.get("title") or "").lower()
    description = (item.get("description") or "").lower()
    brand = (item.get("brand_title") or "").lower()
    full_text = f"{title} {description} {brand}"

    if not any(t in title for t in PSP_TITLE_TERMS):
        log.info(f"  [SKIP no-psp-titolo] {item.get('title')}")
        return False

    for kw in BLACKLIST_KEYWORDS:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    return True


def is_recent(item):
    """Ritorna True solo se l'annuncio è stato creato/aggiornato di recente.

    Usa i timestamp Vinted:
    - created_at_ts: quando l'annuncio è stato creato
    - updated_at_ts: ultima modifica
    - user_updated_at_ts: ultimo aggiornamento fatto dall'utente
    """
    ts = (
        item.get("created_at_ts")
        or item.get("updated_at_ts")
        or item.get("user_updated_at_ts")
    )
    if not ts:
        # Se Vinted non manda timestamp, meglio non filtrare per evitare falsi negativi
        return True

    try:
        # Normalizza "Z" in "+00:00" per datetime.fromisoformat
        ts_norm = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_norm)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        age = datetime.now(timezone.utc) - dt
        if age.total_seconds() < 0:
            # Data nel futuro? Consideriamolo recente
            return True

        if age > timedelta(hours=MAX_ITEM_AGE_HOURS):
            log.info(
                "  [SKIP troppo vecchio] %s (age=%sd, created_at_ts=%s)",
                item.get("title"),
                round(age.total_seconds() / 86400, 2),
                ts,
            )
            return False
        return True
    except Exception as e:
        log.warning(f"  [WARN parse-date] {ts} ({e})")
        # In caso di problemi di parsing meglio non bloccare la notifica
        return True


def fetch_items(scraper, base_url, query):
    try:
        r = scraper.get(
            f"{base_url}/api/v2/catalog/items",
            params={
                "search_text":  query,
                "price_to":     PRICE_MAX,
                "order":        "newest_first",
                "per_page":     96,
                "status_ids[]": 1,
            },
            headers={
                "Accept":           "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          f"{base_url}/catalog?search_text={query}",
            },
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        log.info(f"[{base_url}][{query}] HTTP {r.status_code}, ricevuti: {len(items)}")
        for item in items:
            item["_domain"] = base_url
        return items
    except Exception as e:
        log.error(f"[{base_url}][{query}] Fetch error: {e}")
        return []


def get_price(item):
    price = item.get("price", {})
    if isinstance(price, dict):
        return price.get("amount", "N/D"), price.get("currency_code", "EUR")
    return str(price), item.get("currency", "EUR")


def item_url(item):
    domain = item.get("_domain", "https://www.vinted.it")
    return item.get("url") or f"{domain}/items/{item.get('id', '')}"


def send_summary(items):
    header = f"🎮 *{len(items)} nuov{'o' if len(items)==1 else 'i'} annunci PSP su Vinted!*\n\n"
    lines = []
    for item in items:
        amount, currency = get_price(item)
        title = item.get("title", "N/D")
        url   = item_url(item)
        lines.append(f"🎮 [{title}]({url})\n💶 *{amount} {currency}*\n")

    MAX_LEN = 4000
    chunks, current = [], header
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
    log.info(f"=== Avvio — {datetime.now().strftime('%H:%M:%S')} ===")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,es;q=0.8,fr;q=0.7,de;q=0.6",
    })

    seen_ids, is_first_run = load_seen_ids()
    if is_first_run:
        log.info("*** PRIMO AVVIO: popolo baseline seen_ids senza notificare ***")
    else:
        log.info(f"ID già visti: {len(seen_ids)}")

    all_items_map = {}
    for domain in VINTED_DOMAINS:
        try:
            scraper.get(domain, timeout=15)
        except Exception as e:
            log.warning(f"Warm-up fallito per {domain}: {e}")
        for query in SEARCH_QUERIES:
            for item in fetch_items(scraper, domain, query):
                item_id = str(item.get("id"))
                if item_id not in all_items_map:
                    all_items_map[item_id] = item

    log.info(f"Articoli unici totali: {len(all_items_map)}")

    if is_first_run:
        # Primo avvio: marca tutto come visto, nessuna notifica
        for item_id in all_items_map:
            seen_ids.add(item_id)
        save_seen_ids(seen_ids)
        log.info(f"Baseline salvato: {len(seen_ids)} ID. Dal prossimo run partono le notifiche.")
        # Manda un messaggio di conferma su Telegram
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       f"🔧 Monitor PSP avviato!\nBaseline di {len(seen_ids)} annunci salvato.\nDal prossimo run riceverai solo i nuovi annunci 🚀",
                "parse_mode": "Markdown",
            },
            timeout=15,
        )
        return

    # Run normale: notifica solo i nuovi non ancora visti e recenti
    new_items = [
        item for item_id, item in all_items_map.items()
        if item_id not in seen_ids and is_relevant(item) and is_recent(item)
    ]
    new_items.sort(key=lambda x: int(x.get("id", 0)), reverse=True)

    log.info(f"Nuovi annunci pertinenti: {len(new_items)}")

    if new_items:
        for item in new_items:
            seen_ids.add(str(item.get("id")))
        send_summary(new_items)
        log.info(f"Notifica inviata: {len(new_items)} annunci")
    else:
        log.info("Nessun annuncio nuovo — nessuna notifica.")

    # Marca come visti anche gli annunci non-PSP per non riprocessarli
    for item_id in all_items_map:
        seen_ids.add(item_id)

    save_seen_ids(seen_ids)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
