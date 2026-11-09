#!/usr/bin/env python3
"""kokoro-tts CLI helper -- zero dependencies (stdlib only).

Reads base_url + api_token from config.json next to this file.

Commands:
  pick    --language zh [--gender female] [--scenario audiobook] [--json]
  voices  [--model v1.1-zh] [--language zh] [--gender female]
          [--min-grade A] [--json]
  speak   --text "..." [--voice zf_001] [--out out.mp3] [all synth params]
  sync    [--json]
  health
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"配置文件不存在: {CONFIG_PATH}(复制 config.json 模板并填入 base_url/api_token)")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not cfg.get("base_url"):
        sys.exit("config.json 缺少 base_url")
    return cfg


def call(cfg: dict, method: str, path: str, body: dict | None = None, raw: bool = False):
    url = cfg["base_url"].rstrip("/") + path
    headers = {"User-Agent": "kokoro-tts-skill/1.0"}
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:
            payload = resp.read()
            if raw:
                return resp.status, payload, dict(resp.headers)
            return resp.status, json.loads(payload.decode()), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        sys.exit(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"服务不可达({cfg['base_url']}): {exc.reason}")


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def cmd_pick(args) -> None:
    _, cfg = None, load_config()
    body = {"language": args.language}
    if args.gender:
        body["gender"] = args.gender
    if args.scenario:
        body["scenario"] = args.scenario
    # pick is an MCP-era concept; REST equivalent = filtered voices + local ranking
    status, voices, _ = call(cfg, "GET", "/v1/voices")
    wanted = args.language.lower()
    pool = [
        v for v in voices
        if v["language"].lower().startswith(wanted)
        and (not args.gender or v.get("gender", "").lower() == args.gender.lower())
    ]
    if not pool:
        sys.exit(f"没有匹配音色: language={args.language}, gender={args.gender}")

    scenario = (args.scenario or "").lower()
    audiobook = any(k in scenario for k in ("有声书", "audiobook", "narrat", "旁白", "朗读"))

    def score(v: dict):
        grade = str(v.get("grade") or "").strip("+")[:1].upper()
        points = {"A": 30, "B": 20, "C": 10}.get(grade, 0)
        reasons = []
        if grade:
            reasons.append(f"官方质量评级 {v.get('grade')}")
        if v.get("marks"):
            reasons.append("官方标记 " + "".join(v["marks"]))
            points += 5
        if v.get("note"):
            reasons.append(v["note"])
            points += 3
        if audiobook and v["id"].startswith("zm_"):
            points += 2
            reasons.append("男声播报腔适合有声书/旁白")
        return points, reasons

    ranked = sorted(pool, key=lambda v: score(v)[0], reverse=True)[:5]
    result = {
        "candidates": [
            {"id": v["id"], "language": v["language"], "gender": v.get("gender"),
             "grade": v.get("grade"), "why": "; ".join(score(v)[1]) or "无特记"}
            for v in ranked
        ],
        "hint": "取第一个 candidates 的 id 作为 speak 的 --voice",
    }
    dump(result)


def cmd_voices(args) -> None:
    cfg = load_config()
    query = []
    if args.model:
        query.append(f"model={args.model}")
    if args.language:
        query.append(f"language={args.language}")
    if args.gender:
        query.append(f"gender={args.gender}")
    status, voices, _ = call(cfg, "GET", "/v1/voices" + ("?" + "&".join(query) if query else ""))
    if args.min_grade:
        order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
        threshold = order.get(args.min_grade.upper().strip("+")[:1], 4)
        voices = [
            v for v in voices
            if not v.get("grade") or order.get(str(v["grade"]).strip("+")[:1], 4) <= threshold
        ]
    dump(voices)


def cmd_speak(args) -> None:
    cfg = load_config()
    body = {"input": args.text, "response_format": args.format}
    for key, attr in (
        ("model", "model"), ("voice", "voice"), ("blend_voice", "blend_voice"),
        ("language", "language"), ("phonemes", "phonemes"),
    ):
        value = getattr(args, attr)
        if value:
            body[key] = value
    for key, attr in (
        ("blend_weight", "blend_weight"), ("speed", "speed"), ("volume", "volume"),
        ("peak_target", "peak_target"), ("sentence_pause", "sentence_pause"),
        ("clause_pause", "clause_pause"), ("sample_rate", "sample_rate"),
    ):
        value = getattr(args, attr)
        if value is not None:
            body[key] = value
    body["trim"] = args.trim
    body["continuous"] = args.continuous
    status, payload, headers = call(cfg, "POST", "/v1/audio/speech", body, raw=True)
    lowered = {k.lower(): v for k, v in headers.items()}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    print(f"已保存: {out.resolve()} ({len(payload)} bytes, voice={lowered.get('x-tts-voice', '?')}, "
          f"时长={lowered.get('x-tts-duration-sec', '?')}s)")


def cmd_sync(args) -> None:
    cfg = load_config()
    status, result, _ = call(cfg, "POST", "/v1/admin/sync-voices", None)
    dump(result)


def cmd_health(args) -> None:
    cfg = load_config()
    status, result, _ = call(cfg, "GET", "/health")
    dump(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="kokoro-tts CLI helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pick", help="按场景推荐音色")
    p.add_argument("--language", required=True, help="zh | en-us | ...")
    p.add_argument("--gender", choices=["female", "male"])
    p.add_argument("--scenario", help="场景描述,如 audiobook / 新闻播报")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("voices", help="音色列表")
    p.add_argument("--model", choices=["v1.0", "v1.1-zh"])
    p.add_argument("--language")
    p.add_argument("--gender", choices=["female", "male"])
    p.add_argument("--min-grade", help="A|B|C|D,保留评级不低于该值的")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("speak", help="合成音频")
    p.add_argument("--text", help="要合成的文本")
    p.add_argument("--phonemes", help="音素串(高级,与 --text 二选一)")
    p.add_argument("--out", default="tts-out.mp3", help="输出文件路径")
    p.add_argument("--model", choices=["v1.0", "v1.1-zh"])
    p.add_argument("--voice")
    p.add_argument("--blend-voice", dest="blend_voice")
    p.add_argument("--blend-weight", dest="blend_weight", type=float)
    p.add_argument("--speed", type=float)
    p.add_argument("--volume", type=float)
    p.add_argument("--peak-target", dest="peak_target", type=float)
    p.add_argument("--sentence-pause", dest="sentence_pause", type=float)
    p.add_argument("--clause-pause", dest="clause_pause", type=float)
    p.add_argument("--trim", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--continuous", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--language")
    p.add_argument("--format", choices=["mp3", "wav", "pcm", "opus", "flac"], default="mp3")
    p.add_argument("--sample-rate", dest="sample_rate", type=int)
    p.set_defaults(func=cmd_speak)

    p = sub.add_parser("sync", help="同步官方最新音色")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("health", help="健康检查")
    p.set_defaults(func=cmd_health)

    args = parser.parse_args()
    if args.command == "speak" and not args.text and not args.phonemes:
        parser.error("speak 需要 --text 或 --phonemes")
    args.func(args)


if __name__ == "__main__":
    main()
