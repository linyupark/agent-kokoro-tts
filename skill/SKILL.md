---
name: kokoro-tts
description: Local Kokoro TTS service (REST + token). Use when the user asks to synthesize text-to-speech / generate voice audio / read text aloud, wants voice recommendations, or manages TTS voices (list, pick by scenario, sync official voices). Requires the service to be deployed and reachable.
---

# kokoro-tts skill

Call the self-hosted Kokoro TTS REST service. Configuration (URL + token)
lives in `config.json` next to this file — read it first, never hardcode it
into conversation.

## Configuration

`config.json`:

```json
{ "base_url": "http://<host>:8000", "api_token": "<token>" }
```

If the request fails with 401/connection error, tell the user the service is
unreachable or the token is wrong — do not guess credentials.

## Quick usage (helper script)

`tts.py` is a zero-dependency Python CLI that wraps all endpoints. It reads
`config.json` automatically. All commands support `--json` for machine output.

```bash
# 1. Pick a voice for a scenario (returns ranked candidates + reasons)
python tts.py pick --language zh --gender female --scenario audiobook --json

# 2. List voices with metadata (gender/language/quality grade/notes)
python tts.py voices --model v1.1-zh --gender female --json
python tts.py voices --min-grade A --json        # only A/B graded

# 3. Synthesize to a file (format: mp3/wav/pcm/opus/flac)
python tts.py speak --voice zf_001 --text "千里之行,始于足下。" --out hello.mp3

#    Full-parameter example: pauses, speed, voice blending
python tts.py speak --voice zf_001 --text "第一句。第二句。" \
    --sentence-pause 0.6 --clause-pause 0.3 --speed 1.1 --out book.mp3

# 4. Sync latest official voices (after service admin adds new voices upstream)
python tts.py sync --json
```

## Voice selection guidance

- Always `pick` first when the user doesn't name a voice. Pass their scenario
  words verbatim (e.g. "新闻播报", "lively short video", "calm narration").
- `grade` is the official quality A–F (null = not graded by upstream); voices
  with `marks` (🔥 ❤️) are upstream-flagged highlights.
- v1.1-zh model: 100 Chinese voices (`zf_*` female / `zm_*` male) + 3 English;
  best Chinese quality. v1.0 model: 54 classic voices incl. `af_heart` (A grade).
- Chinese text auto-detected; use `--language` to force.

## Full synthesis parameters

`speak` accepts: `--model v1.0|v1.1-zh`, `--voice`, `--blend-voice/--blend-weight`
(mix two voices), `--speed 0.5-2.0`, `--volume 0.1-3.0`, `--peak-target 0-1`
(loudness normalization; 0 = off), `--sentence-pause 0-5`, `--clause-pause 0-5`
(pause lengths in seconds), `--trim/--no-trim`, `--continuous`, `--language`,
`--format mp3|wav|pcm|opus|flac`, `--sample-rate`, `--phonemes` (advanced).
Defaults are sensible; only pass what the user asked for.

## REST reference (if bypassing tts.py)

- `POST /v1/audio/speech` JSON: `{model, input, voice, speed, volume,
  sentence_pause, clause_pause, response_format, ...}` → audio bytes.
  Auth: `Authorization: Bearer <api_token>`.
- `GET  /v1/voices?model=&language=&gender=` → metadata list.
- `POST /v1/admin/sync-voices` → resync official voices, hot-reload.
- `GET  /health` (no auth).
- Response headers: `X-TTS-Voice`, `X-TTS-Duration-Sec`.

## Notes

- Output is up to 24 kHz audio; long text is fine (auto-chunked server-side).
- If `speak` returns 400 "音色 ... 不在 ... 音色包中", run `sync` then retry.
- Service keeps models warm in ~0.9GB RAM; first call after deploy is slower.
