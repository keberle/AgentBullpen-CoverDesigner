# The Cover Designer api as a container: FastAPI under Uvicorn on port 8080.
# The image carries no settings and no keys: everything comes from environment variables at run time
# (docs/guide/configuration.md, docs/guide/gateway.md). With none set it runs standalone and says no model is configured.
#   docker build -t coverdesigner-api api

# Build: the locked dependencies into a venv, without the dev group (pytest).
# ghcr.io/astral-sh/uv:python3.13-bookworm-slim
FROM ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Run: the same base (so the venv's interpreter is the one it was built against), the venv and the app's own files.
FROM ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY *.py ./
COPY skills/ skills/
COPY styles/ styles/
COPY fonts/ fonts/
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER app
EXPOSE 8080
# No reload. Proxy headers off: the api builds no absolute URLs and reads no client address, so it needs none.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
