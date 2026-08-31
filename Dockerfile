# syntax=docker/dockerfile:1

# --- builder: установка пакета в venv (wheel-only зависимости) ---
FROM python:3.13-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir .[dev]

# --- runtime ---
FROM python:3.13-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 1000 --create-home app \
 && mkdir -p /data \
 && chown app:app /data
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY tests ./tests
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY healthcheck.py /app/healthcheck.py
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && printf 'db_path = "/data/v8help.db"\ncorpus_dir = "/data/corpus"\n' > /app/v8help.toml
ENV PATH=/opt/venv/bin:$PATH \
    V8HELP_CONFIG=/app/v8help.toml \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
USER app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["v8help", "serve", "--http", "--host", "0.0.0.0", "--port", "8000"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "/app/healthcheck.py"]
