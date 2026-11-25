FROM python:3.12-slim
WORKDIR /app
RUN useradd -u 10001 -m qt
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt psycopg2-binary
COPY quanttitan ./quanttitan
COPY scripts ./scripts
ENV QT_DATA_DIR=/data QT_HOST=0.0.0.0 QT_PORT=8765 PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown qt /data
USER qt
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/health')"
CMD ["python", "-m", "quanttitan", "serve", "--data", "/data"]
