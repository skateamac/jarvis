FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml ./
RUN uv pip install --system .[dev]

COPY . .

ENV PYTHONPATH=/app
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
CMD ["uvicorn", "services.scheduler.app:app", "--host", "0.0.0.0", "--port", "8000"]
