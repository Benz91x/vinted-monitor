"""
╔══════════════════════════════════════════════════════════╗
║         VINTED PSP MONITOR — Bot Telegram 24/7          ║
║  Monitora nuovi annunci PSP ogni 5 minuti e notifica     ║
║  istantaneamente via Telegram.                           ║
╚══════════════════════════════════════════════════════════╝

Requisiti: pip install -r requirements.txt
Configurazione: imposta le variabili d'ambiente TELEGRAM_TOKEN e TELEGRAM_CHAT_ID
"""

import os
import json
import time
import logging
import requests
import cloudscraper
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURAZIONE — leggi da variabili d'ambiente
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")    # Token del tuo Bot Telegram
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # Il tuo Chat ID numerico

# Parametri di ricerca Vinted
VINTED_BASE_URL  = "https://www.vinted.it"
SEARCH_QUERY     = "PSP"          # Parola chiave
PRICE_MIN        = 0              # Prezzo minimo (€)
PRICE_MAX        = 60             # Prezzo massimo (€)
CHECK_INTERVAL   = 300            # Ogni 5 minuti (secondi)
RESULTS_PER_PAGE = 96             # Max articoli per richiesta

# File locale per memorizzare gli ID già visti
SEEN_IDS_FILE = "seen_ids.json"

# Quanti ID tenere in memoria (evita che il file cresca all'infinito)
MAX_SEEN_IDS = 2000
TRIM_TO      = 1000

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  1. GESTIONE SEEN IDs  (deduplicazione)
# ══════════════════════════════════════════════

def load_seen_ids() -> set:
    """Carica dal file JSON gli ID degli annunci già notificati."""
    if os.path.exists(SEEN_IDS_FILE):
        try:
            with open(SEEN_IDS_FILE, "r") as f:
                data = json.load(f)
                return set(str(i) for i in data)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Impossibile leggere {SEEN_IDS_FILE}: {e} — parto da zero.")
    return set()


def save_seen_ids(seen_ids: set) -> None:
    """Salva gli ID su file. Taglia la lista se supera MAX_SEEN_IDS."""
    ids_list = list(seen_ids)
    if len(ids_list) > MAX_SEEN_IDS:
        ids_list = ids_list[-TRIM_TO:]   # Tieni solo i più recenti
        seen_ids.clear()
        seen_ids.update(ids_list)
    try:
        with open(SEEN_IDS_FILE, "w") as f:
            json.dump(ids_list, f)
    except IOError as e:
        logger.error(f"Errore salvataggio {SEEN_IDS_FILE}: {e}")


# ══════════════════════════════════════════════
#  2. SESSIONE VINTED  (bypass Cloudflare)
# ══════════════════════════════════════════════

def create_scraper_session() -> cloudscraper.CloudScraper:
    """
    Crea uno scraper con cloudscraper che bypassa Cloudflare.
    Fa una prima richiesta alla homepage per ottenere cookies validi
    (in particolare _vinted_it_session e il CSRF token).
    """
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )
    scraper.headers.update({
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "DNT": "1",
    })
    try:
        logger.info("Inizializzazione sessione Vinted (homepage)...")
        resp = scraper.get(VINTED_BASE_URL, timeout=20)
        resp.raise_for_status()
        logger.info(f"Sessione OK — status {resp.status_code}")
    except Exception as e:
        logger.error(f"Errore apertura homepage Vinted: {e}")
    return scraper


# ══════════════════════════════════════════════
#  3. FETCH ARTICOLI  (API non ufficiale Vinted)
# ══════════════════════════════════════════════

def fetch_vinted_items(scraper: cloudscraper.CloudScraper) -> list:
    """
    Chiama l'API interna di Vinted e ritorna la lista di articoli.

    Endpoint: GET /api/v2/catalog/items
    Parametri principali:
      - search_text : keyword di ricerca
      - price_from  : prezzo minimo
      - price_to    : prezzo massimo
      - order       : newest_first | relevance | ...
      - per_page    : quanti risultati (max 96)
    """
    api_url = f"{VINTED_BASE_URL}/api/v2/catalog/items"
    params  = {
        "search_text": SEARCH_QUERY,
        "price_from":  PRICE_MIN,
        "price_to":    PRICE_MAX,
        "order":       "newest_first",
        "per_page":    RESULTS_PER_PAGE,
        "currency":    "EUR",
    }
    headers = {
        "Accept":          "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":         f"{VINTED_BASE_URL}/catalog?search_text={SEARCH_QUERY}",
    }

    try:
        resp = scraper.get(api_url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        logger.info(f"Ricevuti {len(items)} articoli dall'API Vinted.")
        return items
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error Vinted API: {e.response.status_code} — {e}")
    except requests.exceptions.Timeout:
        logger.error("Timeout nella richiesta all'API Vinted.")
    except Exception as e:
        logger.error(f"Errore generico fetch Vinted: {e}")
    return []


# ══════════════════════════════════════════════
#  4. NOTIFICHE TELEGRAM
# ══════════════════════════════════════════════

def build_item_url(item: dict) -> str:
    """Costruisce l'URL diretto all'annuncio su Vinted."""
    item_id   = item.get("id", "")
    item_slug = item.get("url", "")           # campo "url" = slug testuale
    return f"{VINTED_BASE_URL}/items/{item_id}-{item_slug}"


def build_caption(item: dict) -> str:
    """Costruisce il testo della notifica Telegram in Markdown."""
    title    = item.get("title",    "Titolo non disponibile")
    price    = item.get("price",    "N/D")
    currency = item.get("currency", "EUR")
    brand    = item.get("brand_title", "")
    size     = item.get("size_title",  "")
    cond_map = {1: "Nuovo con etichetta", 2: "Nuovo senza etichetta",
                3: "Ottime condizioni", 4: "Buone condizioni", 5: "Accettabili"}
    condition = cond_map.get(item.get("status"), "")
    url      = build_item_url(item)

    lines = [
        "🎮 *Nuovo annuncio PSP trovato!*",
        "",
        f"📦 *{title}*",
        f"💰 Prezzo: *{price} {currency}*",
    ]
    if brand:
        lines.append(f"🏷️ Brand: {brand}")
    if size:
        lines.append(f"📐 Taglia/Versione: {size}")
    if condition:
        lines.append(f"✅ Condizioni: {condition}")
    lines += ["", f"🔗 [👉 Acquista subito!]({url})"]
    return "\n".join(lines)


def send_telegram_photo(item: dict) -> bool:
    """Invia una notifica con foto (sendPhoto). Ritorna True se riesce."""
    photo_url = item.get("photo", {}).get("full_size_url") or \
                item.get("photo", {}).get("url", "")
    if not photo_url:
        return False

    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    payload  = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "photo":      photo_url,
        "caption":    build_caption(item),
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(endpoint, data=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"sendPhoto fallito: {e} — provo sendMessage.")
        return False


def send_telegram_message(item: dict) -> None:
    """Invia notifica testuale (fallback se la foto non è disponibile)."""
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload  = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     build_caption(item),
        "parse_mode":               "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(endpoint, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Errore invio Telegram (sendMessage): {e}")


def notify(item: dict) -> None:
    """Notifica principale: prova con foto, poi testo puro."""
    title = item.get("title", "N/D")
    if not send_telegram_photo(item):
        send_telegram_message(item)
    logger.info(f"✅ Notifica inviata: '{title}' — {item.get('price')} €")


def send_startup_message() -> None:
    """Messaggio di avvio per confermare che il bot è attivo."""
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    text = (
        "🤖 *Vinted PSP Monitor avviato!*\n"
        f"🔍 Cerco: `{SEARCH_QUERY}` — max {PRICE_MAX}€\n"
        f"⏱️ Controllo ogni {CHECK_INTERVAL // 60} minuti.\n"
        "_Riceverai una notifica per ogni nuovo annuncio._"
    )
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(endpoint, data=payload, timeout=10)
    except Exception:
        pass   # Non critico


# ══════════════════════════════════════════════
#  5. LOOP PRINCIPALE
# ══════════════════════════════════════════════

def main() -> None:
    logger.info("=" * 55)
    logger.info("   VINTED PSP MONITOR — avvio")
    logger.info(f"   Ricerca: '{SEARCH_QUERY}' | Max {PRICE_MAX}€ | ogni {CHECK_INTERVAL}s")
    logger.info("=" * 55)

    # Validazione configurazione
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.critical("❌ TELEGRAM_TOKEN o TELEGRAM_CHAT_ID non impostati! Arresto.")
        return

    # Stato iniziale
    seen_ids = load_seen_ids()
    logger.info(f"ID già visti caricati: {len(seen_ids)}")
    send_startup_message()

    # Sessione anti-bot
    scraper = create_scraper_session()
    cycle   = 0

    while True:
        cycle += 1
        logger.info(f"─── Ciclo #{cycle} — {datetime.now().strftime('%H:%M:%S')} ───")

        # Rinfresca la sessione ogni 20 cicli (~100 min) per evitare cookie scaduti
        if cycle % 20 == 0:
            logger.info("Rinnovo sessione Vinted...")
            scraper = create_scraper_session()

        # Fetch articoli
        items = fetch_vinted_items(scraper)

        if not items:
            logger.warning("Nessun articolo ricevuto (possibile blocco temporaneo). Riprovo al prossimo ciclo.")
        else:
            new_items = [item for item in items if str(item.get("id")) not in seen_ids]

            if new_items:
                logger.info(f"🆕 Trovati {len(new_items)} nuovi annunci!")
                for item in new_items:
                    notify(item)
                    seen_ids.add(str(item.get("id")))
                    time.sleep(1)   # pausa tra notifiche (rispetta rate limit Telegram)
                save_seen_ids(seen_ids)
            else:
                logger.info("Nessun annuncio nuovo in questo ciclo.")

        logger.info(f"💤 Prossimo controllo tra {CHECK_INTERVAL // 60} minuti...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
