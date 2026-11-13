# ---- 1) 模型阶段:只在 scripts/ 或 ./models 变化时重建 ----
# ./models 是"自带模型"目录(仓库里只有 .gitkeep):把已下载好的模型放进去,
# 构建时直接复用、完全不联网;改 app/ 代码也不会让这一层失效。
FROM python:3.11-slim AS models

WORKDIR /app
COPY scripts ./scripts
COPY models ./seed
RUN python scripts/download_models.py /app/models --seed /app/seed

# ---- 2) 运行镜像 ----
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

# 模型从上一阶段拷进来(不再重复下载)
COPY --from=models /app/models /app/models

# 代码放最后:改动 app/ 只重建这一层
COPY app ./app

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
