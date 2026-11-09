"""Download Kokoro models + seed voice bins for the kokoro-tts image.

Sources (fast on CN networks): GitHub kokoro-onnx releases for ONNX models
and voice bins, HF (with mirror fallback) for the zh vocab config.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import urllib.request

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
VOCAB_URLS = [
    "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json",
    "https://hf-mirror.com/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json",
]

FILES = {
    "kokoro-v1.0.fp16.onnx": f"{RELEASE}/kokoro-v1.0.fp16.onnx",
    "kokoro-v1.1-zh.fp16.onnx": f"{RELEASE}/kokoro-v1.1-zh.fp16.onnx",
    "voices-v1.0.bin": f"{RELEASE}/voices-v1.0.bin",
    "voices-v1.1-zh.bin": f"{RELEASE}/voices-v1.1-zh.bin",
    "config.json": VOCAB_URLS,
}

SIZES = {
    "kokoro-v1.0.fp16.onnx": 163527961,
    "kokoro-v1.1-zh.fp16.onnx": 163528759,
    "voices-v1.0.bin": 28214398,
    "voices-v1.1-zh.bin": 53815880,
}


def _download_one(url: str, dest: str, tries: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            print(f"[{attempt}/{tries}] GET {url}")
            request = urllib.request.Request(url, headers={"User-Agent": "kokoro-tts/1.0"})
            tmp = dest + ".part"
            with urllib.request.urlopen(request, timeout=300) as resp, open(tmp, "wb") as fp:
                shutil.copyfileobj(resp, fp)
            os.replace(tmp, dest)
            print(f"    saved {os.path.getsize(dest)} -> {dest}")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(3 * attempt)
    raise RuntimeError(f"下载失败 {url}: {last_error}") from last_error


def download(urls, dest: str, tries: int = 4) -> None:
    if isinstance(urls, str):
        urls = [urls]
    last_error: Exception | None = None
    for url in urls:
        try:
            _download_one(url, dest, tries)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    源 {url} 失败: {exc}")
    raise RuntimeError(f"所有下载源均失败: {last_error}") from last_error


def main() -> None:
    dest_dir = sys.argv[1] if len(sys.argv) > 1 else "/app/models"
    os.makedirs(dest_dir, exist_ok=True)
    for filename, url in FILES.items():
        dest = os.path.join(dest_dir, filename)
        expected = SIZES.get(filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0 and (
            expected is None or os.path.getsize(dest) == expected
        ):
            print(f"已存在,跳过: {dest}")
            continue
        download(url, dest)


if __name__ == "__main__":
    main()
