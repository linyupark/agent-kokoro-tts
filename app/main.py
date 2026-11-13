from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from .audio import MIME
from .config import load_settings
from .engine import EngineError, KokoroEngine, rss_mb
from .schemas import SpeechRequest
from .voice_store import VoiceStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kokoro-tts")


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        app.state.settings = settings
        store = VoiceStore(settings)
        store.ensure_seed()
        engine = KokoroEngine(settings, store)
        app.state.store = store
        app.state.engine = engine

        async def run_sync(groups: list[str] | None = None) -> dict:
            return await asyncio.to_thread(store.sync, groups)

        app.state.run_sync = run_sync

        if settings.sync_on_startup:
            # Run the initial sync in the background: hf-mirror can take many
            # minutes for 157 .pt files, and the API must come up immediately
            # (serving seed voices until the sync hot-reloads).
            async def _startup_sync() -> None:
                try:
                    summary = await run_sync()
                    engine.reload()
                    log.info(
                        "启动音色同步完成(后台): %s",
                        {k: (v.get("local_total"), len(v.get("failed") or [])) for k, v in summary.items()},
                    )
                except Exception as exc:  # noqa: BLE001 - offline boot must still work
                    log.warning("启动音色同步失败(使用种子音色继续): %s", exc)

            app.state.startup_sync_task = asyncio.create_task(_startup_sync())
            log.info("后台音色同步已启动(不阻塞服务启动,期间使用种子音色)")

        from .watchdog import start_memory_watchdog

        app.state.watchdog_task = await start_memory_watchdog(app)
        app.state.idle_task = asyncio.create_task(engine.idle_loop())

        yield

        watchdog = getattr(app.state, "watchdog_task", None)
        if watchdog is not None:
            watchdog.cancel()
        idle_task = getattr(app.state, "idle_task", None)
        if idle_task is not None:
            idle_task.cancel()
        sync_task = getattr(app.state, "startup_sync_task", None)
        if sync_task is not None and not sync_task.done():
            sync_task.cancel()

    app = FastAPI(title="kokoro-tts", version="1.0.0", lifespan=lifespan)

    async def require_token(request: Request) -> None:
        token = request.app.state.settings.api_token
        if not token:
            return
        auth = request.headers.get("authorization", "")
        supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        supplied = supplied or request.query_params.get("token", "")
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing API token")

    @app.get("/health")
    async def health():
        return {"status": "ok", "rss_mb": round(rss_mb(), 1)}

    @app.get("/v1/admin/state", dependencies=[Depends(require_token)])
    async def admin_state():
        return app.state.engine.stats()

    @app.post("/v1/admin/unload", dependencies=[Depends(require_token)])
    async def admin_unload():
        """Drop models/voices/G2P now and return RSS to the OS (manual 释放)."""
        if app.state.engine.inflight:
            raise HTTPException(status_code=409, detail="正在合成,拒绝卸载,请稍后重试")
        before, after = await asyncio.to_thread(app.state.engine.unload, "admin")
        return {
            "status": "unloaded",
            "rss_before_mb": round(before, 1),
            "rss_after_mb": round(after, 1),
        }

    @app.get("/v1/voices", dependencies=[Depends(require_token)])
    async def list_voices(
        model: str | None = None,
        language: str | None = None,
        gender: str | None = None,
    ):
        voices = app.state.store.list_voices(model)
        if language:
            voices = [v for v in voices if v["language"].lower().startswith(language.lower())]
        if gender:
            voices = [v for v in voices if v.get("gender", "").lower() == gender.lower()]
        return voices

    @app.post("/v1/admin/sync-voices", dependencies=[Depends(require_token)])
    async def sync_voices(groups: list[str] | None = None):
        try:
            summary = await app.state.run_sync(groups)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.engine.reload()
        return {"status": "synced, voices hot-reloaded", "detail": summary}

    async def _speech(req: SpeechRequest) -> Response:
        try:
            data, fmt, meta = await app.state.synthesize_speech(
                model=req.model,
                text=req.input,
                phonemes=req.phonemes,
                voice=req.voice,
                blend_voice=req.blend_voice,
                blend_weight=req.blend_weight,
                speed=req.speed,
                volume=req.volume,
                peak_target=req.peak_target,
                sentence_pause=req.sentence_pause,
                clause_pause=req.clause_pause,
                trim=req.trim,
                continuous=req.continuous,
                language=req.language,
                response_format=req.response_format,
                sample_rate=req.sample_rate,
            )
        except EngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        headers = {
            "X-TTS-Voice": meta["voice"],
            "X-TTS-Model": req.model,
            "X-TTS-Source-Sample-Rate": str(meta["sample_rate"]),
            "Content-Disposition": f'inline; filename="speech.{fmt}"',
        }
        if meta["duration_sec"]:
            headers["X-TTS-Duration-Sec"] = f"{meta['duration_sec']:.2f}"
        return Response(content=data, media_type=MIME[fmt], headers=headers)

    @app.post("/v1/audio/speech", dependencies=[Depends(require_token)])
    async def speech_post(req: SpeechRequest) -> Response:
        return await _speech(req)

    @app.get("/v1/audio/speech", dependencies=[Depends(require_token)])
    async def speech_get(
        input: str | None = Query(None, max_length=20000),
        phonemes: str | None = Query(None, max_length=20000),
        model: str = Query(settings.default_model, pattern="^(v1.0|v1.1-zh)$"),
        voice: str | None = None,
        blend_voice: str | None = None,
        blend_weight: float = Query(0.5, ge=0.0, le=1.0),
        speed: float = Query(1.0, ge=0.5, le=2.0),
        volume: float = Query(1.0, ge=0.1, le=3.0),
        peak_target: float | None = Query(None, ge=0.0, le=1.0),
        sentence_pause: float = Query(0.25, ge=0.0, le=5.0),
        clause_pause: float = Query(0.1, ge=0.0, le=5.0),
        trim: bool = True,
        continuous: bool = False,
        language: str | None = None,
        response_format: str = Query("mp3", pattern="^(wav|mp3|pcm|opus|flac)$"),
        sample_rate: int | None = Query(None, ge=8000, le=96000),
    ) -> Response:
        if not input and not phonemes:
            raise HTTPException(status_code=400, detail="input 或 phonemes 必填其一")
        req = SpeechRequest(
            model=model,
            input=input,
            phonemes=phonemes,
            voice=voice,
            blend_voice=blend_voice,
            blend_weight=blend_weight,
            speed=speed,
            volume=volume,
            peak_target=peak_target,
            sentence_pause=sentence_pause,
            clause_pause=clause_pause,
            trim=trim,
            continuous=continuous,
            language=language,
            response_format=response_format,
            sample_rate=sample_rate,
        )
        return await _speech(req)

    async def _synthesize_speech(**kwargs) -> tuple[bytes, str, dict]:
        """Shared synth pipeline.

        Splits the request kwargs: engine params go to the engine, the rest
        (model/response_format/sample_rate) are handled by the pipeline.
        """
        from .audio import convert_audio

        engine_params = {
            "text", "phonemes", "voice", "blend_voice", "blend_weight", "speed",
            "language", "volume", "peak_target", "sentence_pause", "clause_pause",
            "trim", "continuous",
        }
        engine_kwargs = {k: v for k, v in kwargs.items() if k in engine_params}
        engine_kwargs["group"] = kwargs.get("model", settings.default_model)
        try:
            wav, sample_rate, duration, resolved = await app.state.engine.synthesize(**engine_kwargs)
        except EngineError as exc:
            raise ValueError(str(exc)) from exc
        fmt = kwargs.get("response_format") or "mp3"
        data = await convert_audio(wav, "wav", fmt, kwargs.get("sample_rate"))
        return data, fmt, {
            "voice": resolved,
            "sample_rate": sample_rate,
            "duration_sec": duration,
        }

    app.state.synthesize_speech = _synthesize_speech

    return app


app = create_app()
