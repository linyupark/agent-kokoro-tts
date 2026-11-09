from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AudioFormat = Literal["wav", "mp3", "pcm", "opus", "flac"]
ModelGroup = Literal["v1.0", "v1.1-zh"]


class SpeechRequest(BaseModel):
    model: ModelGroup = Field("v1.1-zh", description="音色组: v1.1-zh(中文强) | v1.0(经典54音色)")
    input: str | None = Field(None, max_length=20000, description="要合成的文本")
    phonemes: str | None = Field(
        None, max_length=20000, description="高级:直接给音素串(is_phonemes=True),与 input 二选一"
    )
    voice: str | None = Field(None, description="音色 id,如 zf_001 / af_heart;缺省按语言自动选")
    blend_voice: str | None = Field(None, description="混合的第二音色(同组)")
    blend_weight: float = Field(0.5, ge=0.0, le=1.0, description="混合权重: 0=纯 voice,1=纯 blend_voice")
    speed: float = Field(1.0, ge=0.5, le=2.0, description="语速 0.5~2.0")
    volume: float = Field(1.0, ge=0.1, le=3.0, description="音量 0.1~3.0(在响度归一化后叠加)")
    peak_target: float | None = Field(
        None, ge=0.0, le=1.0, description="峰值归一化目标;0 关闭;缺省用服务端配置(0.95)"
    )
    sentence_pause: float = Field(0.25, ge=0.0, le=5.0, description="句间停顿秒数")
    clause_pause: float = Field(0.1, ge=0.0, le=5.0, description="子句间停顿秒数")
    trim: bool = Field(True, description="修剪首尾静音")
    continuous: bool = Field(False, description="连续模式(少停顿,适合长句)")
    language: str | None = Field(None, description="'zh'|'en-us'|... 缺省自动检测")
    response_format: AudioFormat = Field("mp3", description="输出格式")
    sample_rate: int | None = Field(None, ge=8000, le=96000, description="重采样目标采样率")
