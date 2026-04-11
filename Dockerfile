FROM python:3.11-slim

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copy source
COPY src/ ./src/
COPY config/ ./config/

ENV HEATHROW_NOISE_CONFIG=/app/config/config.yaml
ENV PYTHONUNBUFFERED=1

EXPOSE 47480

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:47480/health/mqtt',timeout=5); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["heathrow-noise", "--log-level", "INFO", "service"]
