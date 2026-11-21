FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home titan
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY titan ./titan
COPY bridges ./bridges
USER titan
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS localhost:8080/api/health || exit 1
CMD ["python", "-m", "titan.cli", "api"]
