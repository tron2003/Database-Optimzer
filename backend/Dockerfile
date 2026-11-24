FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies are installed from pyproject before the source is copied, so a
# code change does not invalidate the dependency layer.
COPY pyproject.toml /srv/pyproject.toml
RUN mkdir -p /srv/app && touch /srv/app/__init__.py \
    && pip install --no-cache-dir -e .

COPY app /srv/app

# Runs unprivileged. The container needs no write access to anything.
RUN useradd --create-home --shell /usr/sbin/nologin dbopt \
    && chown -R dbopt:dbopt /srv
USER dbopt

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
