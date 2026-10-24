FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    SAMPLE_MODE=true \
    DATA_DIR=data \
    DB_PATH=/app/state/lotline.db

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/logs /app/state && useradd --create-home lotline && chown -R lotline /app/logs /app/state
USER lotline

EXPOSE 8000
CMD ["python", "run.py"]
