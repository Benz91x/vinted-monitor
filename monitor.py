import os
import re
import json
import time
import logging
import cloudscraper
import requests
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
PRICE_MAX        = 70
STATE_FILE       = "state.json"
MAX_SEEN         = 2000
MAX_AGE_HOURS    = 24

SEARCH_QUERY = "PSP"

VINTED_DOMAINS = ["it", "es", "fr", "de", "pt", "pl", "be", "nl"]

# ---------------------------------------------------------------------------
# BLACKLIST
# ---------------------------------------------------------------------------
BLACKLIST = [
    "ps4", "ps5", "ps3", "ps2",
    "playstation 4", "playstation 5", "playstation 3", "playstation 2",
    "ps vita", "psvita",
    "xbox", "nintendo", "switch", "wii", "gameboy", "game boy",
    "felpa", "maglietta", "t-shirt", "vestito", "costume",
    "borsa", "zaino", "portafoglio",
    "poster", "quadro", "stampa",
    "amiibo", "funko", "action figure",
    "pokemon", "yugioh", "yu-gi-oh",
    "batteria psp", "battery psp", "bateria psp", "akku psp",
    "batterie pour psp", "batterie psp",
    "chargeur psp", "caricatore psp", "charger psp", "cargador psp",
    "caricabatterie psp",
    "memory stick", "memory card psp",
    "custodia psp", "housse psp", "funda psp", "case psp", "tasche psp",
    "skin psp", "sticker psp",
    "grip psp", "stand psp",
    "umd video", "umd film", "umd movie",
    "modding service", "modding-service",
    "stampa 3d", "3d print",
    "jeu psp", "jogo psp", "juego psp", "spiel psp", "game psp", "gioco psp",
]

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
    "daxter", "ratchet", "jak ",
    "crash ", "tony hawk", "guitar hero",
    "singstar", "katamari", "locoroco", "patapon",
    "hot shots golf", "ape escape",
    "silent hill", "coded arms",
    "outrun", "flatout", "moto gp",
    "dirt ", "wrc ", "v-rally",
    "f1 ", "nba ", "nfl ", "nhl ",
    "rugby ", "tennis ", "golf ", "boxing ",
    "wrestling ", "ufc ", "smackdown",
]

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
def get_age_hours(item_dict):
    try:
        ts = item_dict["photo"]["high_resolution"]["timestamp"]
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        pass
    for field in ("created_at_ts", "updated_at_ts", "last_push_up_at"):
        val = item_dict.get(field)
        if val:
            try:
                dt = datetime.fromtimestamp(int(val), tz=timezone.utc)
                return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                pass
    for field in ("created_at", "updated_at"):
        val = item_dict.get(field)
        if val and isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            except Exception:
                pass
    return None


def is_recent(item_dict):
    age_h = get_age_hours(item_dict)
    if age_h is None:
        return True
    if age_h > MAX_AGE_HOURS:
        log.info(f"  [SKIP {int(age_h)}h fa] {item_dict.get('title')}")
        return False
    return True


# ---------------------------------------------------------------------------
# Filtro
# ---------------------------------------------------------------------------
def is_interesting(item_dict):
    title = (item_dict.get("title") or "").lower().strip()
    description = (item_dict.get("description") or "").lower()
    full_text = f"{title} {description}"

    if not any(t in full_text for t in PSP_TERMS):
        return False

    for kw in BLACKLIST:
        if kw in full_text:
            log.info(f"  [SKIP blacklist='{kw}'] {item_dict.get('title')}")
            return False

    has_bundle = any(h in full_text for h in BUNDLE_HINTS)
    if not has_bundle:
        for game in KNOWN_GAMES:
            if title.startswith(game) or f" {game}" in title:
                log.info(f"  [SKIP gioco='{game}'] {item_dict.get('title')}")
                return False

    log.info(f"  [OK] {item_dict.get('title')}")
    return True


# ---------------------------------------------------------------------------
# VINTED SESSION - metodo Gertje823: homepage prima, poi API
# ---------------------------------------------------------------------------
def make_vinted_session(domain):
    """
    Crea una sessione cloudscraper autenticata per il dominio Vinted specificato.
    STEP 1: GET homepage per ottenere il cookie _vinted_XX_session
    STEP 2: Estrai CSRF token dall'HTML
    STEP 3: Aggiungi X-CSRF-Token agli headers
    Ref: github.com/Gertje823/Vinted-Scraper
    """
    s = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "desktop": True,
        }
    )
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "DNT": "1",
        "Connection": "keep-alive",
    })

    base_url = f"https://www.vinted.{domain}"
    try:
        resp = s.get(base_url, timeout=20)
        log.info(f"[{domain}] homepage status={resp.status_code}, cookies={list(s.cookies.keys())}")

        # Cerca CSRF token nell'HTML
        csrf_match = re.search(r'"CSRF_TOKEN":"([^"]+)"', resp.text)
        if csrf_match:
            s.headers["X-CSRF-Token"] = csrf_match.group(1)
            log.info(f"[{domain}] CSRF token trovato")
        else:
            log.warning(f"[{domain}] CSRF token NON trovato nell'HTML")
    except Exception as e:
        log.error(f"[{domain}] errore homepage: {e}")

    return s, base_url


# ---------------------------------------------------------------------------
# Fetch annunci
# ---------------------------------------------------------------------------
def fetch_domain(domain):
    """
    Ottieni annunci da Vinted per un dominio specifico.
    Usa la sessione autenticata con cookie per chiamare /api/v2/catalog/items.
    """
    s, base_url = make_vinted_session(domain)
    items = []

    for page in [1, 2]:
        params = {
            "search_text":  SEARCH_QUERY,
            "order":        "newest_first",
            "per_page":     "96",
            "page":         str(page),
            "price_to":     str(PRICE_MAX),
        }
        url = f"{base_url}/api/v2/catalog/items"
        try:
            resp = s.get(url, params=params, timeout=20)
            log.info(f"[{domain}] p{page} status={resp.status_code}")

            if resp.status_code == 401:
                log.warning(f"[{domain}] 401 - sessione non valida, riprovo con nuova sessione")
                s, base_url = make_vinted_session(domain)
                resp = s.get(url, params=params, timeout=20)
                log.info(f"[{domain}] p{page} retry status={resp.status_code}")

            if resp.status_code != 200:
                log.warning(f"[{domain}] p{page} risposta non 200: {resp.status_code}")
                break

            data = resp.json()
            page_items = data.get("items", [])
            log.info(f"[{domain}] p{page} items={len(page_items)}")
            items.extend(page_items)

            # Se non ci sono piu' pagine
            pagination = data.get("pagination", {})
            if pagination.get("total_pages", 1) <= page:
                break

        except Exception as e:
            log.error(f"[{domain}] p{page} eccezione: {e}")
            break

        time.sleep(1.5)  # pausa tra pagine dello stesso dominio

    log.info(f"[{domain}] totale items fetch: {len(items)}")
    return items


# ---------------------------------------------------------------------------
# Helpers prezzo/url
# ---------------------------------------------------------------------------
def get_price_str(d):
    price = d.get("price", {})
    if isinstance(price, dict):
        return f"{price.get('amount', 'N/D')} {price.get('currency_code', 'EUR')}"
    currency = d.get("currency", "EUR")
    return f"{price} {currency}"


def item_url(d):
    domain = d.get("_domain", "it")
    url = d.get("url")
    if url:
        return url
    return f"https://www.vinted.{domain}/items/{d.get('id', '')}"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    try:
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
    except Exception as e:
        log.error(f"Telegram error: {e}")


def send_summary(items):
    header = f"\U0001f3ae *{len(items)} nuov{'o' if len(items)==1 else 'i'} annunci PSP!*\n\n"
    lines = []
    for d in items:
        price_str = get_price_str(d)
        title  = d.get("title", "N/D")
        url    = item_url(d)
        domain = d.get("_domain", "")
        age_h  = get_age_hours(d)
        if age_h is not None:
            mins = int(age_h * 60)
            age_str = f" \u23f0 {mins}min fa" if mins < 60 else f" \u23f0 {int(age_h)}h fa"
        else:
            age_str = ""
        lines.append(
            f"\U0001f3ae [{title}]({url})\n"
            f"\U0001f4b6 *{price_str}*{age_str} \u2022 vinted.{domain}\n"
        )

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

    state, is_first_run = load_state()
    seen_ids = set(state["seen_ids"])
    log.info(f"seen_ids: {len(seen_ids)} | primo_avvio: {is_first_run}")

    all_psp = {}  # id -> dict

    for domain in VINTED_DOMAINS:
        raw_items = fetch_domain(domain)
        for item in raw_items:
            item["_domain"] = domain
            try:
                iid = int(item.get("id", 0))
            except (ValueError, TypeError):
                continue
            if iid == 0:
                continue
            if iid not in all_psp and is_interesting(item) and is_recent(item):
                all_psp[iid] = item
        time.sleep(2)  # pausa tra domini

    log.info(f"Annunci interessanti totali: {len(all_psp)}")

    if not all_psp:
        log.warning("Nessun annuncio trovato. State NON aggiornato.")
        return

    if is_first_run:
        state["seen_ids"] = list(all_psp.keys())
        save_state(state)
        log.info(f"Baseline silenziosa: {len(all_psp)} annunci salvati.")
        return

    new_items = [
        d for iid, d in all_psp.items()
        if iid not in seen_ids
    ]
    new_items.sort(key=lambda x: int(x.get("id", 0)), reverse=True)

    log.info(f"Nuovi da notificare: {len(new_items)}")

    if new_items:
        send_summary(new_items)
        log.info(f"Notifica inviata: {len(new_items)} annunci")
    else:
        log.info("Nessun annuncio nuovo.")

    state["seen_ids"] = list(seen_ids | set(all_psp.keys()))
    save_state(state)
    log.info("=== Fine ciclo ===")


if __name__ == "__main__":
    main()
