FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY polymarket_bot/ ./polymarket_bot/

# Price-history cache lives here; mount a volume to persist it across restarts
RUN mkdir -p /root/.cache/polymarket_backtest

# Non-root user for security
RUN useradd -m botuser && chown -R botuser /app /root/.cache/polymarket_backtest
USER botuser

# Credentials are injected at runtime via environment variables — never baked in
ENV PRIVATE_KEY=""
ENV API_KEY=""
ENV API_SECRET=""
ENV API_PASSPHRASE=""
ENV FUNDER=""

CMD ["python", "-m", "polymarket_bot"]
