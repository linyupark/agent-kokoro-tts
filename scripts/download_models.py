"""Download Kokoro models + seed voice bins for the kokoro-tts image.

Sources (fast on CN networks): GitHub kokoro-onnx releases for ONNX models
and voice bins, HF (with mirror fallback) for the zh vocab config.

Everything is idempotent: a file whose size matches SIZES is never re-fetched,
and `--seed DIR` reuses models the caller already has (the Dockerfile passes
the build context's ./models, i.e. a copy you dropped in yourself).
"""

from __future__ import annotations

import argparse
import os
import shutil
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


def _download_one(url: str, dest: str, expected: int | None = None, tries: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        tmp = dest + ".part"
        try:
            print(f"[{attempt}/{tries}] GET {url}")
            request = urllib.request.Request(url, headers={"User-Agent": "kokoro-tts/1.0"})
            with urllib.request.urlopen(request, timeout=300) as resp:
                declared = resp.headers.get("Content-Length")
                declared = int(declared) if declared and declared.isdigit() else None
                written = 0
                with open(tmp, "wb") as fp:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        fp.write(chunk)
                        written += len(chunk)
            # 连接被提前掐断时 urllib 不会报错,必须自己核对长度
            want = expected if expected is not None else declared
            if want is not None and written != want:
                raise RuntimeError(f"下载不完整: {written} != {want} bytes")
            os.replace(tmp, dest)
            print(f"    saved {written} -> {dest}")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    重试: {exc}")
            if os.path.exists(tmp):
                os.remove(tmp)
            time.sleep(3 * attempt)
    raise RuntimeError(f"下载失败 {url}: {last_error}") from last_error


def download(urls, dest: str, expected: int | None = None, tries: int = 4) -> None:
    if isinstance(urls, str):
        urls = [urls]
    last_error: Exception | None = None
    for url in urls:
        try:
            _download_one(url, dest, expected, tries)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    源 {url} 失败: {exc}")
    raise RuntimeError(f"所有下载源均失败: {last_error}") from last_error


def _size_ok(path: str, expected: int | None) -> bool:
    if not os.path.isfile(path):
        return False
    size = os.path.getsize(path)
    return size > 0 and (expected is None or size == expected)


def _seed_copy(seed_dir: str, filename: str, dest: str, expected: int | None) -> bool:
    """Copy an already-downloaded model from seed_dir; False if unusable."""
    src = os.path.join(seed_dir, filename)
    if not _size_ok(src, expected):
        return False
    tmp = f"{dest}.part"
    shutil.copyfile(src, tmp)
    if not _size_ok(tmp, expected):
        os.remove(tmp)
        return False
    os.replace(tmp, dest)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", nargs="?", default="/app/models", help="模型输出目录")
    parser.add_argument("--seed", default=None, help="已有模型的目录,命中就直接复用、不联网")
    args = parser.parse_args(argv)

    os.makedirs(args.dest, exist_ok=True)
    reused = skipped = fetched = 0
    for filename, url in FILES.items():
        dest = os.path.join(args.dest, filename)
        expected = SIZES.get(filename)
        if _size_ok(dest, expected):
            print(f"已存在,跳过: {dest}")
            skipped += 1
            continue
        if args.seed and _seed_copy(args.seed, filename, dest, expected):
            print(f"复用已有模型: {filename} ({os.path.getsize(dest)} bytes)")
            reused += 1
            continue
        download(url, dest, expected)
        fetched += 1
    print(f"模型就绪: 复用 {reused} 个, 跳过 {skipped} 个, 下载 {fetched} 个 → {args.dest}")


if __name__ == "__main__":
    main()
