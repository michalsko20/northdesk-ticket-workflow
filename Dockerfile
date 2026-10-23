FROM python:3.12.13-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home app \
    && mkdir /state && chown app:app /state

FROM base AS runtime
USER app
CMD ["uvicorn", "northdesk.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM base AS tests
RUN uv sync --locked
COPY scripts/download_test_server.py /tmp/download_test_server.py
RUN python /tmp/download_test_server.py /opt/temporal-test-server
COPY tests ./tests
COPY scripts ./scripts
ENV TEMPORAL_TEST_SERVER=/opt/temporal-test-server
USER app
CMD ["pytest", "-q", "-p", "no:cacheprovider"]
