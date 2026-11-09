"""Kokoro engine: lazily loads v1.0 / v1.1-zh models, exposes rich params."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import threading

import numpy as np

from .langs import detect_language
from .voice_store import VoiceStore

log = logging.getLogger(__name__)

DEFAULT_VOICE = {"v1.1-zh": {"zh": "zf_001"}, "v1.0": {"en-us": "af_heart", "zh": "zf_xiaoxiao"}}

# Latin word runs inside Chinese text (brand names, tech terms, ...)
_LATIN_RUN = re.compile(r"[A-Za-z]+(?:['\u2019\-][A-Za-z]+)*")


class EngineError(RuntimeError):
    pass


class KokoroEngine:
    def __init__(self, settings, store: VoiceStore) -> None:
        self._s = settings
        self._store = store
        self._kokoro: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._thread_lock = threading.Lock()
        self._zh_g2p = None
        self._zh_g2p_error: str | None = None

    async def _ensure_zh_g2p(self):
        if self._zh_g2p is not None or self._zh_g2p_error is not None:
            return

        def _load():
            from misaki import zh as misaki_zh

            return misaki_zh.ZHG2P(version="1.1")

        try:
            self._zh_g2p = await asyncio.to_thread(_load)
        except Exception as exc:  # noqa: BLE001
            self._zh_g2p_error = f"{type(exc).__name__}: {exc}"
            log.warning("misaki 中文 G2P 不可用(%s),中文请求将报错", self._zh_g2p_error)

    def _zh_phonemes(self, kokoro, text: str) -> str:
        """ZHG2P for Chinese; Latin runs via espeak so brand names survive."""
        if self._zh_g2p is None:
            raise EngineError(f"中文 G2P 不可用({self._zh_g2p_error});请安装 misaki-fork[zh]")
        if not _LATIN_RUN.search(text):
            phonemes, _ = self._zh_g2p(text)
            return phonemes
        phonemize_en = kokoro.tokenizer.phonemize
        pieces: list[str] = []
        last = 0
        for match in _LATIN_RUN.finditer(text):
            before = text[last : match.start()]
            if before.strip():
                phonemes, _ = self._zh_g2p(before)
                pieces.append(phonemes)
            pieces.append(phonemize_en(match.group(0), "en-us"))
            last = match.end()
        tail = text[last:]
        if tail.strip():
            phonemes, _ = self._zh_g2p(tail)
            pieces.append(phonemes)
        return " ".join(p for p in pieces if p)

    def _model_path(self, group: str) -> str:
        return self._s.model_v1_0 if group == "v1.0" else self._s.model_v1_1

    async def _get(self, group: str):
        if group not in self._locks:
            self._locks[group] = asyncio.Lock()
        async with self._locks[group]:
            model = self._kokoro.get(group)
            if model is not None:
                return model

            def _load():
                from kokoro_onnx import Kokoro

                kwargs = {}
                if group == "v1.1-zh" and self._s.vocab_config:
                    from pathlib import Path

                    if Path(self._s.vocab_config).exists():
                        kwargs["vocab_config"] = self._s.vocab_config
                return Kokoro(self._model_path(group), str(self._store.bin_path(group)), **kwargs)

            model = await asyncio.to_thread(_load)
            self._kokoro[group] = model
            log.info("kokoro 模型已加载: %s", group)
            return model

    def reload(self) -> None:
        """Drop cached models so freshly synced voices take effect."""
        with self._thread_lock:
            self._kokoro.clear()

    def resolve_style(self, group: str, voices: dict[str, np.ndarray], voice: str | None,
                      blend_voice: str | None, blend_weight: float, lang: str) -> tuple[str | np.ndarray, str]:
        """Return (voice id or blended style array, resolved voice label)."""
        default = DEFAULT_VOICE.get(group, {}).get(lang) or next(iter(voices))
        voice = voice or default
        if voice not in voices:
            raise EngineError(f"音色 '{voice}' 不在 {group} 音色包中;先同步或换音色")
        if not blend_voice:
            return voice, voice
        if blend_voice not in voices:
            raise EngineError(f"混合音色 '{blend_voice}' 不在 {group} 音色包中")
        blend_weight = min(max(blend_weight, 0.0), 1.0)
        style = (1.0 - blend_weight) * voices[voice] + blend_weight * voices[blend_voice]
        return style, f"{voice}~{blend_voice}@{blend_weight:.2f}"

    async def synthesize(
        self,
        *,
        group: str,
        text: str | None,
        phonemes: str | None,
        voice: str | None,
        blend_voice: str | None,
        blend_weight: float,
        speed: float,
        language: str | None,
        volume: float,
        peak_target: float,
        sentence_pause: float,
        clause_pause: float,
        trim: bool,
        continuous: bool,
    ) -> tuple[bytes, int, float, str]:
        """Returns (wav bytes, sample rate, duration sec, resolved voice)."""
        kokoro = await self._get(group)
        voices = await asyncio.to_thread(self._store.load_group, group)

        if phonemes:
            lang = language or "en-us"
            source, is_phonemes = phonemes.strip(), True
        else:
            text = (text or "").strip()
            if not text:
                raise EngineError("input 不能为空")
            lang = detect_language(text, language)
            if lang == "zh":
                await self._ensure_zh_g2p()
                source, is_phonemes = self._zh_phonemes(kokoro, text), True
            else:
                source, is_phonemes = text, False

        style, resolved = self.resolve_style(group, voices, voice, blend_voice, blend_weight, lang)
        speed = min(max(speed, 0.5), 2.0)
        sentence_pause = min(max(sentence_pause, 0.0), 5.0)
        clause_pause = min(max(clause_pause, 0.0), 5.0)

        def _run():
            return kokoro.create(
                source,
                voice=style,
                speed=speed,
                lang=lang,
                is_phonemes=is_phonemes,
                trim=trim,
                sentence_pause=sentence_pause,
                clause_pause=clause_pause,
                continuous=continuous,
            )

        samples, sample_rate = await asyncio.to_thread(_run)
        samples = np.asarray(samples, dtype=np.float32)
        peak = self._s.default_peak_target if peak_target is None else peak_target
        if peak and peak > 0.0 and samples.size:
            max_abs = float(np.max(np.abs(samples)))
            if max_abs > 1e-6:
                samples = samples * (peak / max_abs)
        if volume != 1.0:
            samples = np.clip(samples * volume, -1.0, 1.0)

        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue(), int(sample_rate), len(samples) / float(sample_rate), resolved
