FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (scraper + Flight parser + API).
COPY linkedin_scrape.py flight_parse.py api.py ./

# Cookies + API key are injected at runtime via env vars (see .env / -e flags),
# NOT baked into the image. Do not COPY .env into the image.
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
