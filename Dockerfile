FROM python:3.12-slim AS base

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY worker ./worker
COPY migrations ./migrations
COPY alembic.ini wsgi.py ./

RUN chown -R appuser:appuser /app
USER appuser
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["gunicorn", "-w", "3", "-k", "gthread", "--threads", "4", \
     "-b", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
