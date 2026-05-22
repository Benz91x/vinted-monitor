import os
import json
import time
import requests
import cloudscraper
import logging
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 70
STATE_FILE       = "state.json"
RETRY_ATTEMPTS   = 3
RETRY_DELAY      = 4
MAX_SEEN         = 2000
MAX_AGE_HOURS    = 24

VINTED_DOMAINS = [
    "https://www.vinted.it",
    "https://www.vinted.es",
    "https://www.vinted.fr",
    "https://www.vinted.de",
    "https://www.vinted.pt",
    "https://www.vinted.pl",
    "https://www.vinted.be",
    "https://www.vinted.nl",
]

SEARCH_QUERIES = [
    "PSP console",
    "PSP 1000",
    "PSP 2000",
    "PSP 3000",
    "PSP go",
    "PlayStation Portable console",
    "sony psp console",
    "console portatile sony",
    "consola portatil sony",
    "console portable sony psp",
]

# ---------------------------------------------------------------------------
# Parole che indicano CONSOLE (almeno una deve essere presente)
# ---------------------------------------------------------------------------
CONSOLE_TERMS = [
    "console",
    "consola",
    "consolle",
    "handheld",
    "portatile",
    "portatil",
    "portable",
    "spielkonsole",
    "konsole",
    "psp 1000", "psp 2000", "psp 3000", "psp 4000",
    "psp fat", "psp slim", "psp go",
    "psp-1", "psp-2", "psp-3",
    "psp-e100", "psp-n100",
    "play station portable",
    "playstation portable",
    # annunci tipo "PSP + giochi" o "PSP con giochi" spesso sono bundle con console
    "con giochi", "avec jeux", "con juegos", "with games", "met games",
    "mit spielen", "com jogos",
    "+ giochi", "+ jeux", "+ juegos",
    "bundle", "lote", "lot ",
    # venditore che scrive solo "PSP" intendendo la console (titolo molto corto)
]

# ---------------------------------------------------------------------------
# Parole che indicano SOLO accessorio/gioco/film → scarta sempre
# ---------------------------------------------------------------------------
ACCESSORY_ONLY_TERMS = [
    "batterie", "batteria", "battery", "bateria", "akku",
    "chargeur", "caricatore", "caricabatterie", "charger", "cargador", "carregador", "ladegerät",
    "custodia", "housse", "funda", "case ", "tasche", "hoes",
    "memory stick", "memory card", "scheda memoria", "carte memoire",
    "umd video", "umd film", "umd movie",
    "film psp", "movie psp",
    "crazy kung", "training day",   # film UMD specifici
    "modding service", "modding-service",
    "microsd", "micro sd",
    "grip", "stand", "dock",
    "skin ", "sticker", "decal",
]

# ---------------------------------------------------------------------------
# Blacklist categorica
# ---------------------------------------------------------------------------
BLACKLIST_KEYWORDS = [
    "ps4", "ps5", "ps3", "ps2",
    "playstation 4", "playstation 5", "playstation 3", "playstation 2",
    "ps vita", "psvita", "vita",
    "xbox", "nintendo", "switch", "wii",
    "pokemon", "yugioh", "yu-gi-oh",
    "amiibo", "funko",
    "borsa", "zaino", "poster",
    "felpa", "maglietta", "t-shirt",
    "stampato", "stampa 3d", "3d print",
]

PSP_TERMS = [
    "psp",
    "playstation portable",
    "play station portable",
    "ps portable",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
                if isinstance(data.get("seen_ids"), list) and len(data["seen_ids"]) > 0:
                    return data, False
        except Exception:
            pass
    return {"seen_ids": []}, True


def save_state(state):
    state["seen_ids"] = state["seen_ids"][-MAX_SEEN:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    log.info(f"State salvato: {len(state['seen_ids'])} seen_ids")


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------
def get_age_hours(item):
    try:
        ts = item["photo"]["high_resolution"]["timestamp"]
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        pass
    for field in ("created_at_ts", "updated_at_ts", "last_push_up_at"):
        val = item.get(field)
        if val:
            try:
                dt = datetime.fromtimestamp(int(val), tz=timezone.utc)
                return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                pass
    for field in ("created_at", "updated_at"):
        val = item.get(field)
        if val and isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                pass
    return None


def is_recent(item):
    age_h = get_age_hours(item)
    if age_h is None:
        log.warning(f"  [NO TIMESTAMP - accettato] {item.get('title')}")
        return True
    if age_h > MAX_AGE_HOURS:
        log.info(f"  [SKIP {int(age_h)}h fa] {item.get('title')}")
        return False
    return True


# ---------------------------------------------------------------------------
# Filtro: deve essere una CONSOLE PSP, non un accessorio/gioco
# ---------------------------------------------------------------------------
def is_console(item):
    title       = (item.get("title") or "").lower()
    description = (item.get("description") or "").lower()
    full_text   = f"{title} {description}"

    # 1. Scarta subito se è palesemente solo un accessorio
    for kw in ACCESSORY_ONLY_TERMS:
        if kw in full_text:
            log.info(f"  [SKIP accessorio='{kw}'] {item.get('title')}")
            return False

    # 2. Scarta blacklist
    for kw in BLACKLIST_KEYWORDS:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    # 3. Deve contenere almeno un termine PSP
    if not any(t in full_text for t in PSP_TERMS):
        log.info(f"  [SKIP no-psp-term] {item.get('title')}")
        return False

    # 4. Deve contenere almeno un termine che indica console/hardware
    #    OPPURE il titolo è molto corto (tipo "PSP" o "PSP nera") → probabile console
    title_words = len(title.split())
    has_console_term = any(t in full_text for t in CONSOLE_TERMS)
    is_short_title   = title_words <= 5  # titoli corti come "PSP", "PSP nera", "Sony PSP"

    if has_console_term or is_short_title:
        log.info(f"  [OK console] {item.get('title')}")
        return True

    # 5. Titolo più lungo senza termine console → probabilmente solo un gioco
    log.info(f"  [SKIP probabile-gioco] {item.get('title')}")
    return False


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
            log.info(f"[{base_url}][{query}] -> {len(items)} items")
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
        title  = item.get("title", "N/D")
        url    = item_url(item)
        domain = item.get("_domain", "").replace("https://www.", "")
        age_h  = get_age_hours(item)
        if age_h is not None:
            mins = int(age_h * 60)
            age_str = f" \u23f0 {mins}min fa" if mins < 60 else f" \u23f0 {int(age_h)}h fa"
        else:
            age_str = ""
        lines.append(f"\U0001f3ae [{title}]({url})\n\U0001f4b6 *{amount} {currency}*{age_str} \u2022 {domain}\n")

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
        "Accept-Language": "it-IT,it;q=0.9,es;q=0.8,fr;q=0.7,de;q=0.6,pt;q=0.5,nl;q=0.4,pl;q=0.3",
    })

    state, is_first_run = load_state()
    seen_ids = set(state["seen_ids"])
    log.info(f"seen_ids caricati: {len(seen_ids)} | primo_avvio: {is_first_run}")

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
                if iid not in all_psp and is_console(item) and is_recent(item):
                    all_psp[iid] = item
            time.sleep(1)

    log.info(f"Console PSP recenti: {len(all_psp)}")

    if not all_psp:
        log.warning("Nessuna console PSP trovata. State NON aggiornato.")
        return

    if is_first_run:
        state["seen_ids"] = list(all_psp.keys())
        save_state(state)
        log.info(f"Baseline silenziosa: {len(all_psp)} annunci salvati.")
        return

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

    state["seen_ids"] = list(seen_ids | set(all_psp.keys()))
    save_state(state)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
