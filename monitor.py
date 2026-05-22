import os
import json
import time
import requests
import cloudscraper
import logging
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 70
STATE_FILE       = "state.json"
RETRY_ATTEMPTS   = 3
RETRY_DELAY      = 5
MAX_SEEN         = 2000
MAX_AGE_HOURS    = 24

# Una sola query generica per dominio: Vinted ordina per newest_first
# Filtrare lato nostro è molto più efficace che fare 12 query diverse
SEARCH_QUERY = "PSP"

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

# ---------------------------------------------------------------------------
# BLACKLIST: scarta questi prodotti con certezza assoluta
# ---------------------------------------------------------------------------
BLACKLIST = [
    # Console NON-PSP
    "ps4", "ps5", "ps3", "ps2",
    "playstation 4", "playstation 5", "playstation 3", "playstation 2",
    "ps vita", "psvita",
    "xbox", "nintendo", "switch", "wii", "gameboy", "game boy",
    # Abbigliamento / oggettistica
    "felpa", "maglietta", "t-shirt", "vestito", "costume",
    "borsa", "zaino", "portafoglio",
    "poster", "quadro", "stampa",
    "amiibo", "funko", "action figure",
    "pokemon", "yugioh", "yu-gi-oh",
    # Accessori puri (senza console)
    "batteria psp", "battery psp", "bateria psp", "akku psp",
    "batterie pour psp", "batterie psp",
    "chargeur psp", "caricatore psp", "charger psp", "cargador psp",
    "caricabatterie psp",
    "memory stick", "memory card psp",
    "custodia psp", "housse psp", "funda psp", "case psp", "tasche psp",
    "skin psp", "sticker psp",
    "grip psp", "stand psp",
    # Film UMD
    "umd video", "umd film", "umd movie",
    # Servizi
    "modding service", "modding-service",
    "stampa 3d", "3d print",
    # Giochi singoli in lingua straniera (frasi chiare)
    "jeu psp", "jogo psp", "juego psp", "spiel psp", "game psp", "gioco psp",
]

# Giochi PSP noti: scartati SOLO se nel titolo NON ci sono indizi di bundle/console
KNOWN_GAMES = [
    "god of war", "gran turismo", "need for speed",
    "fifa ", "pro evolution soccer", "pes ",
    "assassin", "grand theft auto", "gta",
    "call of duty", "metal gear", "final fantasy",
    "monster hunter", "kingdom hearts",
    "naruto", "dragon ball", "one piece",
    "tekken", "ridge racer", "burnout",
    "midnight club", "socom", "wipeout",
    "lumines", "lego ", "star wars",
    "harry potter", "batman", "spider-man", "spiderman",
    "sims ", "les sims",
    "world cup", "coupe du monde", "coppa del mondo",
    "pro cycling", "tour de france",
    "daxter", "ratchet", "jak ",
    "crash ", "tony hawk", "guitar hero",
    "singstar", "katamari", "locoroco", "patapon",
    "hot shots golf", "ape escape",
    "pursuit force", "syphon filter", "killzone",
    "resistance", "silent hill", "coded arms",
    "outrun", "flatout", "moto gp",
    "dirt ", "wrc ", "v-rally",
    "f1 ", "nba ", "nfl ", "nhl ",
    "rugby ", "tennis ", "golf ", "boxing ",
    "wrestling ", "ufc ", "smackdown",
]

# Se il titolo contiene almeno uno di questi -> è un bundle/console, non solo gioco
BUNDLE_HINTS = [
    "console", "consola", "consolle",
    "portatile", "portable", "portatil",
    "psp 1", "psp 2", "psp 3", "psp go", "psp slim", "psp fat",
    "con giochi", "avec jeux", "con juegos", "with games", "com jogos",
    "+ giochi", "+ jeux", "bundle", "lotto", "lot ", "lote",
    "scheda", "memory", "completo", "completa", "komplett",
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
# Filtro principale
# ---------------------------------------------------------------------------
def is_interesting(item):
    title = (item.get("title") or "").lower().strip()
    description = (item.get("description") or "").lower()
    full_text = f"{title} {description}"

    # 1. Deve contenere un termine PSP
    if not any(t in full_text for t in PSP_TERMS):
        log.info(f"  [SKIP no-psp] {item.get('title')}")
        return False

    # 2. Blacklist categorica
    for kw in BLACKLIST:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item.get('title')}")
            return False

    # 3. Gioco noto senza bundle hint -> scarta
    has_bundle = any(h in full_text for h in BUNDLE_HINTS)
    if not has_bundle:
        for game in KNOWN_GAMES:
            if title.startswith(game) or f" {game}" in title:
                log.info(f"  [SKIP gioco='{game}'] {item.get('title')}")
                return False

    log.info(f"  [OK] {item.get('title')}")
    return True


# ---------------------------------------------------------------------------
# Fetch: UNA query per dominio, due pagine (96+96 = fino a 192 annunci freschi)
# ---------------------------------------------------------------------------
def fetch_items(scraper, base_url):
    results = {}
    for page in (1, 2):
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                r = scraper.get(
                    f"{base_url}/api/v2/catalog/items",
                    params={
                        "search_text":   SEARCH_QUERY,
                        "price_to":      PRICE_MAX,
                        "order":         "newest_first",
                        "per_page":      96,
                        "page":          page,
                        "status_ids[]":  1,
                    },
                    headers={
                        "Accept":           "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer":          f"{base_url}/catalog?search_text={SEARCH_QUERY}&order=newest_first",
                    },
                    timeout=25,
                )
                r.raise_for_status()
                items = r.json().get("items", [])
                log.info(f"[{base_url}] pagina {page} -> {len(items)} items")
                for item in items:
                    item["_domain"] = base_url
                    try:
                        results[int(item["id"])] = item
                    except (KeyError, ValueError):
                        pass
                break  # pagina ok, esci dal retry loop
            except Exception as e:
                log.warning(f"[{base_url}] pagina {page} tentativo {attempt} fallito: {e}")
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_DELAY)
        else:
            log.error(f"[{base_url}] pagina {page} tutti i tentativi falliti")
        time.sleep(2)  # pausa tra pagina 1 e pagina 2

    return list(results.values())


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
        # Warm-up cookie/session per il dominio
        try:
            scraper.get(domain, timeout=15)
            time.sleep(2)
        except Exception as e:
            log.warning(f"Warm-up fallito per {domain}: {e}")

        for item in fetch_items(scraper, domain):
            try:
                iid = int(item["id"])
            except (KeyError, ValueError):
                continue
            if iid not in all_psp and is_interesting(item) and is_recent(item):
                all_psp[iid] = item

        time.sleep(3)  # pausa tra un dominio e il prossimo

    log.info(f"Annunci interessanti: {len(all_psp)}")

    if not all_psp:
        log.warning("Nessun annuncio trovato. State NON aggiornato.")
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
