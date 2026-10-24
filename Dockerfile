FROM python:3.12.13-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir /state && chown app:app /state \
    && chmod 750 /state

FROM base AS runtime
USER app
# --limit-concurrency prevents unbounded connection accumulation;
# --timeout-keep-alive (5s) is below the default client pool idle timeout.
CMD ["uvicorn", "northdesk.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--limit-concurrency", "256", "--timeout-keep-alive", "5", "--h11-max-incomplete-event-size", "16384"]

FROM base AS tests
RUN uv sync --locked
COPY scripts/download_test_server.py /tmp/download_test_server.py
RUN python /tmp/download_test_server.py /opt/temporal-test-server
COPY tests ./tests
COPY scripts ./scripts
COPY Makefile ./
ENV TEMPORAL_TEST_SERVER=/opt/temporal-test-server
ENV RUFF_CACHE_DIR=/tmp/ruff-cache MYPY_CACHE_DIR=/tmp/mypy-cache
USER app
CMD ["sh", "scripts/check.sh"]
