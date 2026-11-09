from __future__ import annotations

import re

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def detect_language(text: str, language: str | None) -> str:
    """Normalize a language tag; 'zh' when CJK ideographs present."""
    if language:
        lang = language.strip().lower()
        if lang.startswith("zh"):
            return "zh"
        if lang.startswith("en"):
            return "en-us"
        return lang
    return "zh" if _CJK.search(text) else "en-us"


VOICE_LANGUAGE_BY_PREFIX = {
    "z": "zh",
    "a": "en-US",
    "b": "en-GB",
    "e": "es",
    "f": "fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-BR",
}
