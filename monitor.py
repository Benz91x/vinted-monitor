import os
import json
import time
import requests
import logging
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

# Domini supportati dalla libreria vinted-api-wrapper
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
    # item_dict e' il dict grezzo dalla libreria
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
        return True  # accetta se non c'e' timestamp
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
# Fetch usando vinted-api-wrapper (gestisce cookie session automaticamente)
# ---------------------------------------------------------------------------
def fetch_domain(domain):
    """Usa vinted-api-wrapper per ottenere gli annunci piu' recenti."""
    try:
        from vinted import Vinted
        vinted = Vinted(domain=domain)
        # Pagina 1: 96 item, newest_first
        result1 = vinted.search(
            query=SEARCH_QUERY,
            per_page=96,
            order="newest_first",
            price_to=PRICE_MAX,
        )
        items = list(result1) if result1 else []
        # Pagina 2
        try:
            result2 = vinted.search(
                query=SEARCH_QUERY,
                per_page=96,
                page=2,
                order="newest_first",
                price_to=PRICE_MAX,
            )
            if result2:
                items += list(result2)
        except Exception as e2:
            log.warning(f"[{domain}] pagina 2 fallita: {e2}")

        log.info(f"[{domain}] trovati {len(items)} item totali")
        return items
    except Exception as e:
        log.error(f"[{domain}] fetch fallito: {e}")
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def item_to_dict(item, domain):
    """Converte oggetto Item o dict in dict normalizzato."""
    if isinstance(item, dict):
        d = item
    else:
        # oggetto dataclass della libreria
        try:
            import dataclasses
            d = dataclasses.asdict(item)
        except Exception:
            d = item.__dict__ if hasattr(item, "__dict__") else {}
    d["_domain"] = domain
    return d


def get_price_str(d):
    price = d.get("price", {})
    if isinstance(price, dict):
        return f"{price.get('amount', 'N/D')} {price.get('currency_code', 'EUR')}"
    # la libreria puo' restituire price come stringa o float
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
        for raw in raw_items:
            d = item_to_dict(raw, domain)
            try:
                iid = int(d.get("id", 0))
            except (ValueError, TypeError):
                continue
            if iid == 0:
                continue
            if iid not in all_psp and is_interesting(d) and is_recent(d):
                all_psp[iid] = d
        time.sleep(2)  # pausa cortese tra domini

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
