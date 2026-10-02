FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/forge
RUN groupadd --system forge && useradd --system --gid forge --home-dir /srv/forge forge
COPY . .
RUN python -m pip install --no-cache-dir . \
    && mkdir -p /srv/forge/data /srv/forge/media /srv/forge/logs \
    && chown -R forge:forge /srv/forge
USER forge
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
