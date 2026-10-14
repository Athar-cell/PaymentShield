FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV and headless environments
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-connector.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-connector.txt

COPY . .

# Pre-train and serialize the ML model artifacts
RUN python scripts/train_models.py

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn src.live.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
