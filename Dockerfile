FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/tmp/hf

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Bake both models + seed voice bins at build time (GitHub releases, fast).
RUN python scripts/download_models.py /app/models

ENV KOKORO_V1_0_MODEL=/app/models/kokoro-v1.0.fp16.onnx \
    KOKORO_V1_1_MODEL=/app/models/kokoro-v1.1-zh.fp16.onnx \
    SEED_BIN_V1_0=/app/models/voices-v1.0.bin \
    SEED_BIN_V1_1=/app/models/voices-v1.1-zh.bin \
    KOKORO_VOCAB_CONFIG=/app/models/config.json \
    DATA_DIR=/app/data \
    OUTPUT_DIR=/app/data/output

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
