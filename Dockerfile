# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-alpine3.22
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.31

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv

RUN apk add --no-cache font-dejavu

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

RUN addgroup -S -g "${APP_GID}" app \
    && adduser -S -D -H -u "${APP_UID}" -G app app

ENV PATH="/app/.venv/bin:${PATH}" \
    APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random \
    HOME=/tmp \
    DATABASE_URL=sqlite:////app/instance/koinoxrista_app.db \
    PDF_FONT_PATH=/usr/share/fonts/dejavu/DejaVuSans.ttf \
    PDF_FONT_BOLD_PATH=/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder /usr/share/fonts/dejavu/DejaVuSans.ttf /usr/share/fonts/dejavu/DejaVuSans-Bold.ttf /usr/share/fonts/dejavu/
COPY --chown=app:app run.py ./
COPY --chown=app:app koinoxrista ./koinoxrista
COPY --chown=app:app docker-entrypoint.sh ./

RUN mkdir -p /app/instance \
    && chown app:app /app/instance \
    && chmod 0700 /app/instance \
    && chmod 0555 /app/docker-entrypoint.sh

USER app:app
EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=1", "--worker-class=sync", "--timeout=60", "--graceful-timeout=30", "--keep-alive=5", "--max-requests=1000", "--max-requests-jitter=100", "--worker-tmp-dir=/tmp", "--access-logfile=-", "--error-logfile=-", "--capture-output", "--umask=0077", "run:app"]
