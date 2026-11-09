from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: str
    models_dir: str
    model_v1_0: str
    model_v1_1: str
    seed_bin_v1_0: str
    seed_bin_v1_1: str
    vocab_config: str
    hf_endpoint: str
    repo_v1_0: str
    repo_v1_1: str
    api_token: str
    public_base_url: str
    default_peak_target: float
    sync_on_startup: bool
    default_model: str


def load_settings() -> Settings:
    return Settings(
        data_dir=os.getenv("DATA_DIR", "/app/data"),
        models_dir=os.getenv("MODELS_DIR", "/app/models"),
        model_v1_0=os.getenv("KOKORO_V1_0_MODEL", "/app/models/kokoro-v1.0.fp16.onnx"),
        model_v1_1=os.getenv("KOKORO_V1_1_MODEL", "/app/models/kokoro-v1.1-zh.fp16.onnx"),
        seed_bin_v1_0=os.getenv("SEED_BIN_V1_0", "/app/models/voices-v1.0.bin"),
        seed_bin_v1_1=os.getenv("SEED_BIN_V1_1", "/app/models/voices-v1.1-zh.bin"),
        vocab_config=os.getenv("KOKORO_VOCAB_CONFIG", "/app/models/config.json"),
        hf_endpoint=os.getenv("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/"),
        repo_v1_0=os.getenv("OFFICIAL_REPO_V1_0", "hexgrad/Kokoro-82M"),
        repo_v1_1=os.getenv("OFFICIAL_REPO_V1_1", "hexgrad/Kokoro-82M-v1.1-zh"),
        api_token=os.getenv("API_TOKEN", "").strip(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
        default_peak_target=_env_float("DEFAULT_PEAK_TARGET", 0.95),
        sync_on_startup=_env_bool("SYNC_ON_STARTUP", True),
        default_model=os.getenv("DEFAULT_MODEL", "v1.1-zh"),
    )
