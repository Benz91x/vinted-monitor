# 🎮 Guida Completa — Vinted PSP Monitor

> Monitora Vinted ogni 5 minuti, ricevi notifiche Telegram istantanee,
> gira 24/7 a **costo zero** anche a PC spento.

---

## 📋 Indice

1. [Crea il Bot Telegram](#1-crea-il-bot-telegram)
2. [Ottieni il tuo Chat ID](#2-ottieni-il-tuo-chat-id)
3. [Testa lo script in locale](#3-testa-lo-script-in-locale-opzionale)
4. [Deploy GRATUITO su Koyeb](#4-deploy-gratuito-su-koyeb-raccomandato)
5. [Alternativa: Hugging Face Spaces](#5-alternativa-hugging-face-spaces)
6. [Domande frequenti e troubleshooting](#6-domande-frequenti)

---

## 1. Crea il Bot Telegram

> Tempo stimato: **2 minuti** — completamente gratuito.

### Passaggi

1. Apri Telegram e cerca **`@BotFather`** (il bot ufficiale con la spunta blu).
2. Scrivi `/start`, poi `/newbot`.
3. BotFather ti chiede il **nome** del bot (es. `Vinted PSP Notifiche`).
4. Ti chiede lo **username** del bot — deve finire in `bot` (es. `mio_psp_monitor_bot`).
5. BotFather risponde con un messaggio tipo:

```
Done! Congratulations on your new bot.
Use this token to access the HTTP API:

7123456789:AAHdqTcvCH1vGWJxfSeofSs0K67PNON4MY4

Keep your token secure and store it safely.
```

6. **Copia e salva questo token** — è il tuo `TELEGRAM_TOKEN`.

> ⚠️ Non condividere mai il token con nessuno.

---

## 2. Ottieni il tuo Chat ID

Il Chat ID è il numero univoco che identifica la tua chat con il bot.

### Metodo più semplice

1. Cerca su Telegram il bot **`@userinfobot`**.
2. Scrivi `/start` — ti risponde con i tuoi dati, incluso **Id: 123456789**.
3. **Quel numero è il tuo `TELEGRAM_CHAT_ID`**.

### Metodo alternativo (via API)

1. Prima invia qualsiasi messaggio al tuo bot appena creato (es. "ciao").
2. Apri nel browser:
   ```
   https://api.telegram.org/botTUO_TOKEN/getUpdates
   ```
   (sostituisci `TUO_TOKEN` con il token reale)
3. Cerca nel JSON il campo `"id"` dentro `"from"` — quello è il tuo Chat ID.

---

## 3. Testa lo script in locale (opzionale)

Se vuoi provarlo prima del deploy:

```bash
# 1. Clona o crea la cartella del progetto
mkdir vinted_monitor && cd vinted_monitor

# 2. Installa le dipendenze
pip install -r requirements.txt

# 3. Imposta le variabili d'ambiente (Linux/Mac)
export TELEGRAM_TOKEN="7123456789:AAHdqTcvCH1vGWJxfSeofSs0K67PNON4MY4"
export TELEGRAM_CHAT_ID="123456789"

# Su Windows (PowerShell)
# $env:TELEGRAM_TOKEN="7123456789:..."
# $env:TELEGRAM_CHAT_ID="123456789"

# 4. Avvia
python monitor.py
```

Se tutto funziona, ricevi su Telegram il messaggio:
> 🤖 *Vinted PSP Monitor avviato!*

---

## 4. Deploy GRATUITO su Koyeb ✅ RACCOMANDATO

**Koyeb** è la piattaforma migliore per questo scopo:
- ✅ Piano gratuito con **1 servizio sempre attivo** (no sleep mai)
- ✅ Deploy da GitHub in 3 click
- ✅ Variabili d'ambiente sicure
- ✅ Nessuna carta di credito richiesta

### Passo 1 — Carica il codice su GitHub

1. Vai su [github.com](https://github.com) e crea un account gratuito (se non ce l'hai).
2. Clicca **"New repository"** → chiama il repo `vinted-monitor`.
3. Seleziona **Private** (per nascondere il codice).
4. Clicca **"Create repository"**.
5. Nella pagina del repo, clicca **"uploading an existing file"**.
6. Carica questi 3 file:
   - `monitor.py`
   - `requirements.txt`
   - `Dockerfile`
7. Clicca **"Commit changes"**.

### Passo 2 — Crea account Koyeb

1. Vai su [app.koyeb.com](https://app.koyeb.com) → **"Sign up"**.
2. Registrati con GitHub (il più veloce).
3. Accetta i termini.

### Passo 3 — Crea il servizio

1. Dashboard Koyeb → clicca **"Create Service"**.
2. Scegli **"GitHub"** come sorgente.
3. Autorizza Koyeb ad accedere al tuo GitHub.
4. Seleziona il repo `vinted-monitor`.
5. Koyeb rileva automaticamente il `Dockerfile` → conferma **"Docker"**.
6. **Configura le variabili d'ambiente** (sezione "Environment variables"):

   | Name                | Value                          |
   |---------------------|--------------------------------|
   | `TELEGRAM_TOKEN`    | `7123456789:AAHdq...`          |
   | `TELEGRAM_CHAT_ID`  | `123456789`                    |

7. Nella sezione **"Regions"** scegli `Frankfurt` (più vicino all'Italia = meno latenza).
8. **Instance type**: seleziona **"Free"** (nano).
9. Clicca **"Deploy"**.

### Passo 4 — Verifica

- Il deploy impiega 2-3 minuti.
- Nella tab **"Logs"** del servizio vedrai:
  ```
  2024-XX-XX [INFO] VINTED PSP MONITOR — avvio
  2024-XX-XX [INFO] Sessione OK — status 200
  2024-XX-XX [INFO] Ricevuti 96 articoli dall'API Vinted.
  ```
- Sul telefono arriva il messaggio **"🤖 Vinted PSP Monitor avviato!"**.

✅ **Fatto! Lo script gira 24/7 anche a PC spento.**

---

## 5. Alternativa: Hugging Face Spaces

Se preferisci Hugging Face (più conosciuto in Italia):

1. Vai su [huggingface.co](https://huggingface.co) → crea account gratuito.
2. Clicca **"New Space"**.
3. Nome: `vinted-psp-monitor`.
4. **SDK: seleziona "Docker"** (non Gradio/Streamlit).
5. Visibilità: **Private**.
6. Clicca **"Create Space"**.
7. Vai su **"Files"** → carica `monitor.py`, `requirements.txt`, `Dockerfile`.
8. Vai su **"Settings"** → **"Repository secrets"** → aggiungi:
   - `TELEGRAM_TOKEN` = il tuo token
   - `TELEGRAM_CHAT_ID` = il tuo chat ID
9. Lo Space si avvia automaticamente ad ogni push.

> ⚠️ Gli Space gratuiti di HuggingFace vanno in **sleep dopo 48 ore di inattività**.
> Per evitarlo, usa uno script esterno (es. UptimeRobot gratuito) che fa ping ogni 30 min,
> oppure preferisci Koyeb che non ha questo problema.

---

## 6. Domande Frequenti

### ❓ "Ricevo notifiche duplicate al riavvio"
Normale: il filesystem di Koyeb è temporaneo, quindi `seen_ids.json` viene perso.
Solo al primo avvio dopo un riavvio potresti ricevere notifiche vecchie.
Soluzione avanzata: usa un database gratuito come **Supabase** o **PlanetScale** per
persistere gli ID.

### ❓ "Ricevo errori 403 da Vinted"
Vinted a volte blocca gli IP dei cloud provider. Soluzioni:
- Aspetta 30-60 minuti: spesso si sblocca da solo.
- Cambia regione del server su Koyeb (prova `Washington DC`).
- `cloudscraper` aggiorna automaticamente i headers ad ogni sessione.

### ❓ "Come cambio la keyword o il prezzo massimo?"
Modifica le variabili all'inizio di `monitor.py`:
```python
SEARCH_QUERY = "PSP"   # Cambia qui la ricerca
PRICE_MAX    = 60      # Cambia il prezzo massimo
```
Poi fai un nuovo commit su GitHub: Koyeb si aggiorna automaticamente.

### ❓ "Posso cercare più keyword insieme?"
Sì! Avvia più servizi Koyeb (ogni account ha 2 servizi free), uno per keyword.
Oppure aggiungi un secondo `fetch_vinted_items()` con `SEARCH_QUERY = "PlayStation Portable"`.

### ❓ "Il servizio Koyeb si è fermato, cosa faccio?"
Vai su Koyeb → Seleziona il servizio → **"Redeploy"**. Il servizio riparte in 1 minuto.

---

## 🗂️ Struttura del progetto

```
vinted_monitor/
├── monitor.py          # Script principale
├── requirements.txt    # Dipendenze Python
├── Dockerfile          # Per il deploy su cloud
└── seen_ids.json       # Creato automaticamente a runtime
```

---

## 🔒 Sicurezza

- Non committare mai `TELEGRAM_TOKEN` direttamente nel codice.
- Usa sempre le **variabili d'ambiente** della piattaforma di hosting.
- Tieni il repository GitHub **privato**.

---

*Guida realizzata per Vinted Italia (vinted.it). Per altri paesi cambia `VINTED_BASE_URL`.*
*Es: `https://www.vinted.fr` per la Francia, `https://www.vinted.de` per la Germania.*
