# Foundry Hosted Agent container.
# IMPORTANT: the hosting platform requires linux/amd64 images. On Apple Silicon
# build with:  docker build --platform linux/amd64 -t excel-csv-agent .
# (With azd / ACR Tasks the remote build already targets amd64 — no local Docker.)
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# AgentServerHost serves on 0.0.0.0:${PORT:-8088}; Foundry injects PORT.
EXPOSE 8088

CMD ["python", "-m", "app.host"]
