from __future__ import annotations

import asyncio

FORMAT_ARGS = {
    "wav": ["-f", "wav"],
    "mp3": ["-f", "mp3", "-b:a", "96k"],
    "pcm": ["-f", "s16le"],
    "opus": ["-c:a", "libopus", "-f", "ogg"],
    "flac": ["-f", "flac"],
}

MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "pcm": "audio/pcm",
    "opus": "audio/ogg",
    "flac": "audio/flac",
}


async def convert_audio(
    data: bytes, src_fmt: str, dst_fmt: str, sample_rate: int | None
) -> bytes:
    if src_fmt == dst_fmt and sample_rate is None:
        return data
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
    if sample_rate:
        args += ["-ar", str(sample_rate)]
    args += FORMAT_ARGS[dst_fmt] + ["-vn", "pipe:1"]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(data)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 转换到 {dst_fmt} 失败: {stderr.decode(errors='replace')[-500:]}"
        )
    return stdout
