FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/gateway

COPY gateway/pyproject.toml gateway/README.md ./
COPY gateway/alicedev_gateway ./alicedev_gateway
COPY gateway/static ./static

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 gateway

USER gateway
EXPOSE 8080

CMD ["python", "-m", "alicedev_gateway"]
