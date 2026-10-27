# One image for the API, Celery worker and scheduler; the command selects the role.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
    && pip install . \
    && rm -rf app build *.egg-info

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-deps . && mkdir -p /scratch && chown app:app /scratch

ENV WORK_DIR=/scratch
USER app
EXPOSE 8000 9101
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# Test image: adds dev dependencies and the test suite.
FROM base AS test
USER root
COPY tests ./tests
RUN pip install ".[dev]"
USER app
CMD ["pytest", "-q"]
