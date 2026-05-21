# ─────────────────────────────────────────────────────────
#  Dockerfile — Vinted PSP Monitor
#  Compatibile con: Koyeb, Hugging Face Spaces, Render,
#                   qualsiasi host Docker gratuito.
# ─────────────────────────────────────────────────────────

FROM python:3.11-slim

# Cartella di lavoro all'interno del container
WORKDIR /app

# Copia i file del progetto
COPY requirements.txt .
COPY monitor.py .

# Installa dipendenze (--no-cache riduce la dimensione dell'immagine)
RUN pip install --no-cache-dir -r requirements.txt

# Il file seen_ids.json viene creato a runtime nella stessa cartella
# (su Koyeb il filesystem è efimero: gli ID vengono persi al riavvio,
#  ma è accettabile — si ricevono al massimo duplicati al primo avvio.)

# Variabili d'ambiente da impostare nella dashboard della piattaforma:
# ENV TELEGRAM_TOKEN=...
# ENV TELEGRAM_CHAT_ID=...

# Avvia lo script
CMD ["python", "-u", "monitor.py"]
