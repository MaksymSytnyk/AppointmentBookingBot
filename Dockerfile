FROM python:3.12-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml README.md ./
COPY appointment_booking ./appointment_booking
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python .

FROM python:3.12-slim
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 atelier && useradd --uid 10001 --gid atelier --create-home atelier
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=atelier:atelier alembic.ini ./
COPY --chown=atelier:atelier alembic ./alembic
COPY --chown=atelier:atelier scripts ./scripts
USER atelier
CMD ["python", "-m", "appointment_booking.app.main"]
