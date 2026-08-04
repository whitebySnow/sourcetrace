# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.10.0 AS uv

FROM python:3.12-slim-bookworm

ARG TORCH_RUNTIME=cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/apps/api/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    touch README.md \
    && uv sync --project apps/api --frozen --no-dev --no-install-project --extra ${TORCH_RUNTIME}

COPY README.md ./README.md
COPY apps/api ./apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --project apps/api --frozen --no-dev --extra ${TORCH_RUNTIME} \
    && python -c "import torch; expected = '${TORCH_RUNTIME}'; assert (torch.version.cuda is None) == (expected == 'cpu'), (expected, torch.version.cuda)"

EXPOSE 8000

CMD ["uvicorn", "sourcetrace.main:app", "--host", "0.0.0.0", "--port", "8000"]
