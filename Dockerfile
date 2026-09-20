FROM ghcr.io/astral-sh/uv:0.12.14@sha256:1946145b8706ad9e5c0e79a513f9e324b58d5e38126bb2c8b7dbfca61febeb45 AS uv
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
RUN useradd --uid 10001 --create-home app
COPY main.py ./
COPY delivery ./delivery
COPY migrations ./migrations
COPY scripts ./scripts
COPY proto ./proto

FROM base AS test
RUN uv sync --locked --no-install-project
COPY tests ./tests
USER app
CMD ["pytest", "-q", "-p", "no:cacheprovider"]

FROM base AS app
USER app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
