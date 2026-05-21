import os
import json
import time
import requests
import cloudscraper
import logging
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 70
STATE_FILE       = "state.json"
RETRY_ATTEMPTS   = 3
RETRY_DELAY      = 4
MAX_SEEN         = 1000

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
]

BLACKLIST_KEYWORDS = [
    "ps4", "ps5", "ps3", "ps2",
    "playstation 4", "playstation 5", "playstation 3", "playstation 2",
    "xbox", "nintendo", "switch", "wii",
    "carta", "carte", "card", "cards",
    "pokemon", "yugioh", "yu-gi-oh",
    "amiibo", "funko",
    "borsa", "zaino", "poster",
    "felpa", "maglietta", "t-shirt",
    "umd film", "umd movie",
    "stampato", "stampa 3d", "3d print",
    "supporto", "supporti",
]

PSP_TERMS = ["psp", "playstation portable", "ps portable"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State — salva la lista degli ID PSP gia' visti (ultimi MAX_SEEN)
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                if isinstance(data.get("seen_ids"), list):
                    return data
        except Exception:
            pass
    return {"seen_ids": []}


def save_state(state):
    state["seen_ids"] = state["seen_ids"][-MAX_SEEN:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State salvato: {len(state['seen_ids'])} seen_ids")


# ---------------------------------------------------------------------------
# Filtro pertinenza PSP
# ---------------------------------------------------------------------------
def is_relevant(item):
    title       = (item.get("title") or "").lower()
    description = (item.get("description") or "").lower()
    brand       = (item.get("brand_title") or "").lower()
    full_text   = f"{title} {description} {brand}"

    if not any(t in full_text for t in PSP_TERMS):
        return False

    for kw in BLACKLIST_KEYWORDS:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    return True


# ---------------------------------------------------------------------------
# Fetch con retry
# ---------------------------------------------------------------------------
def fetch_items(scraper, base_url, query):
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            r = scraper.get(
                f"{base_url}/api/v2/catalog/items",
                params={
                    "search_text":   query,
                    "price_to":      PRICE_MAX,
                    "order":         "newest_first",
                    "per_page":      96,
                    "status_ids[]": 1,
                },
                headers={
                    "Accept":           "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer":          f"{base_url}/catalog?search_text={query}",
                },
                timeout=25,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            log.info(f"[{base_url}][{query}] tentativo {attempt} -> {len(items)} items")
            for item in items:
                item["_domain"] = base_url
            return items
        except Exception as e:
            log.warning(f"[{base_url}][{query}] tentativo {attempt} fallito: {e}")
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_DELAY)
    log.error(f"[{base_url}][{query}] tutti i tentativi falliti")
    return []


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def get_price(item):
    price = item.get("price", {})
    if isinstance(price, dict):
        return price.get("amount", "N/D"), price.get("currency_code", "EUR")
    return str(price), item.get("currency", "EUR")


def item_url(item):
    domain = item.get("_domain", "https://www.vinted.it")
    return item.get("url") or f"{domain}/items/{item.get('id', '')}"


def send_telegram(text):
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


def send_summary(items):
    header = f"\U0001f3ae *{len(items)} nuov{'o' if len(items)==1 else 'i'} annunci PSP!*\n\n"
    lines = []
    for item in items:
        amount, currency = get_price(item)
        title = item.get("title", "N/D")
        url   = item_url(item)
        lines.append(f"\U0001f3ae [{title}]({url})\n\U0001f4b6 *{amount} {currency}*\n")

    MAX_LEN, chunks, current = 4000, [], header
    for line in lines:
        if len(current) + len(line) + 1 > MAX_LEN:
            chunks.append(current)
            current = line
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current)
    for chunk in chunks:
        send_telegram(chunk)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info(f"=== Avvio {datetime.now().strftime('%H:%M:%S')} ===")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False})
    scraper.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9,es;q=0.8,fr;q=0.7,de;q=0.6",
    })

    state    = load_state()
    seen_ids = set(state["seen_ids"])
    log.info(f"seen_ids caricati: {len(seen_ids)}")

    # --- primo avvio: seen_ids vuoto ---
    is_first_run = len(seen_ids) == 0

    # Scarica e filtra solo PSP rilevanti
    all_psp = {}
    for domain in VINTED_DOMAINS:
        try:
            scraper.get(domain, timeout=15)
            time.sleep(1)
        except Exception as e:
            log.warning(f"Warm-up fallito per {domain}: {e}")
        for query in SEARCH_QUERIES:
            for item in fetch_items(scraper, domain, query):
                try:
                    iid = int(item["id"])
                except (KeyError, ValueError):
                    continue
                if iid not in all_psp and is_relevant(item):
                    all_psp[iid] = item
            time.sleep(1)

    log.info(f"Annunci PSP rilevanti trovati: {len(all_psp)}")

    if not all_psp:
        log.warning("Nessun annuncio PSP trovato — API bloccata? State NON aggiornato.")
        return

    if is_first_run:
        # Salva tutti gli ID attuali come baseline, non notificare
        state["seen_ids"] = list(all_psp.keys())
        save_state(state)
        log.info(f"Baseline: {len(all_psp)} annunci PSP salvati come gia' visti.")
        send_telegram(
            f"\U0001f527 *Monitor PSP avviato!*\n"
            f"Trovati {len(all_psp)} annunci esistenti (non notificati).\n"
            f"Dal prossimo run riceverai solo i nuovi \U0001f680"
        )
        return

    # Notifica solo PSP non ancora visti
    new_items = [
        item for iid, item in all_psp.items()
        if iid not in seen_ids
    ]
    new_items.sort(key=lambda x: int(x["id"]), reverse=True)

    log.info(f"Nuovi da notificare: {len(new_items)}")

    if new_items:
        send_summary(new_items)
        log.info(f"Notifica inviata per {len(new_items)} annunci")
    else:
        log.info("Nessun annuncio nuovo.")

    # Aggiorna seen_ids con tutti i PSP visti in questo run
    state["seen_ids"] = list(seen_ids | set(all_psp.keys()))
    save_state(state)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
