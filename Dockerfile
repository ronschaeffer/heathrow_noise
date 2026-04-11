# --- Builder stage ---
FROM python:3.11-slim AS builder
WORKDIR /build

RUN pip install --no-cache-dir poetry poetry-plugin-export

COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --without dev -o requirements.txt

COPY . .
RUN poetry build -f wheel

# --- Runtime stage ---
FROM python:3.11-slim
WORKDIR /app

COPY --from=builder /build/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && rm requirements.txt

COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

COPY config/config.yaml /app/config/config.yaml

RUN mkdir -p /app/config

ENV HEATHROW_NOISE_CONFIG=/app/config/config.yaml
ENV PYTHONUNBUFFERED=1

EXPOSE 47480

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:47480/health/mqtt',timeout=5); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["heathrow-noise", "--log-level", "INFO", "service"]
