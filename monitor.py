import os, json, requests, cloudscraper, logging
from datetime import datetime, timezone

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 60
SEEN_IDS_FILE    = "seen_ids.json"

# Domini Vinted da monitorare
VINTED_DOMAINS = [
    "https://www.vinted.it",
    "https://www.vinted.es",
    "https://www.vinted.fr",
    "https://www.vinted.de",
]

# ~10M ID/giorno su tutti i domini combinati; usiamo soglia conservativa
MAX_AGE_HOURS = 24
ID_PER_HOUR   = 416_000

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
    "pokemon", "pok\u00e9mon", "yugioh", "yu-gi-oh", "magic the gathering", "mtg",
    "amiibo", "funko",
    "cover", "custodia", "borsa", "zaino", "poster", "tazza",
    "felpa", "maglietta", "t-shirt",
    "umd film", "umd movie",
    "president safety",  # marca di abbigliamento che si chiama PSP
    "p.s.p",             # altra marca non gaming
]

# Termini che DEVONO essere nel titolo (almeno uno)
PSP_TITLE_TERMS = [
    "psp",
    "playstation portable",
    "playstation-portable",
    "ps portable",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def get_min_id_threshold(items_sample):
    if not items_sample:
        return 0
    max_id = max(int(item.get("id", 0)) for item in items_sample)
    threshold = max_id - (ID_PER_HOUR * MAX_AGE_HOURS)
    log.info(f"ID max nel fetch: {max_id} | Soglia {MAX_AGE_HOURS}h: {threshold}")
    return threshold


def load_seen_ids():
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE) as f:
            return set(str(i) for i in json.load(f))
    return set()


def save_seen_ids(seen_ids, min_id_threshold):
    ids_to_keep = {sid for sid in seen_ids if int(sid) >= min_id_threshold}
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(ids_to_keep)[-2000:], f)
    log.info(f"seen_ids salvati: {len(ids_to_keep)} (rimossi {len(seen_ids)-len(ids_to_keep)} vecchi)")


def is_fresh(item, min_id_threshold):
    item_id = int(item.get("id", 0))
    if item_id < min_id_threshold:
        log.info(f"  [SKIP vecchio ID={item_id}] {item.get('title')}")
        return False
    return True


def is_relevant(item):
    title = (item.get("title") or "").lower()
    description = (item.get("description") or "").lower()
    brand = (item.get("brand_title") or "").lower()
    full_text = f"{title} {description} {brand}"

    # 1. Deve contenere un termine PSP nel titolo
    if not any(t in title for t in PSP_TITLE_TERMS):
        log.info(f"  [SKIP no-psp-titolo] {item.get('title')}")
        return False

    # 2. Non deve contenere keyword nella blacklist
    for kw in BLACKLIST_KEYWORDS:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    return True


def fetch_items(scraper, base_url, query):
    try:
        # Warm-up sul dominio specifico
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
        # Aggiungi il dominio ad ogni item per costruire l'URL corretto
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
    log.info(f"=== Avvio \u2014 {datetime.now().strftime('%H:%M:%S')} ===")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,es;q=0.8,fr;q=0.7,de;q=0.6",
    })

    seen_ids = load_seen_ids()
    log.info(f"ID gi\u00e0 visti: {len(seen_ids)}")

    all_items_map = {}
    for domain in VINTED_DOMAINS:
        # Warm-up su ogni dominio
        try:
            scraper.get(domain, timeout=15)
        except Exception as e:
            log.warning(f"Warm-up fallito per {domain}: {e}")

        for query in SEARCH_QUERIES:
            for item in fetch_items(scraper, domain, query):
                item_id = str(item.get("id"))
                if item_id not in all_items_map:
                    all_items_map[item_id] = item

    log.info(f"Articoli unici totali (pre-filtro): {len(all_items_map)}")

    min_id = get_min_id_threshold(list(all_items_map.values()))

    new_items = [
        item for item_id, item in all_items_map.items()
        if item_id not in seen_ids
        and is_fresh(item, min_id)
        and is_relevant(item)
    ]
    log.info(f"Nuovi annunci pertinenti e recenti: {len(new_items)}")

    if new_items:
        for item in new_items:
            seen_ids.add(str(item.get("id")))
        send_summary(new_items)
        log.info(f"Notifica inviata: {len(new_items)} annunci")
    else:
        log.info("Nessun annuncio nuovo \u2014 nessuna notifica.")

    save_seen_ids(seen_ids, min_id)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
