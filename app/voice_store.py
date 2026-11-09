"""Voice registry + official voice sync.

Group "v1.0"   <- hexgrad/Kokoro-82M        voices/*.pt   (54, v0.19/v1.0 era)
Group "v1.1-zh" <- hexgrad/Kokoro-82M-v1.1-zh voices/*.pt (103, zh+en)

Merged per-group npz bins live under DATA_DIR; official .pt files are
converted with the pure-numpy reader (no torch) and overlaid on top of the
release seed bins, so new official voices appear after a sync without
rebuilding the image.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import time
import urllib.request
from pathlib import Path

import numpy as np

from .langs import VOICE_LANGUAGE_BY_PREFIX
from .pt_reader import load_pt_tensor

log = logging.getLogger(__name__)


def strip_md(text: str) -> str:
    """Strip markdown emphasis (incl. italic _..._) from a table cell."""
    return re.sub(r"[*_`]", "", text).strip()


class VoiceStore:
    # Descriptions for voices the official VOICES.md doesn't cover
    # (v1.1-zh's 103 numbered voices) — derived from id + community feedback.
    KNOWN_TRAITS = {
        "af_maple": {"note": "v1.1-zh 自带英文女声,音色沉稳"},
        "af_sol": {"note": "v1.1-zh 自带英文女声,音色明亮"},
        "bf_vale": {"note": "v1.1-zh 自带英式英文女声"},
        "zf_001": {"note": "女声,标准播音腔,清晰自然(社区常用推荐)"},
        "zf_002": {"note": "女声,柔和偏温柔"},
        "zm_010": {"note": "男声,浑厚稳重(社区常用推荐)"},
        "zm_009": {"note": "男声,清晰自然"},
    }

    def __init__(self, settings) -> None:
        self._s = settings
        self.data_dir = Path(settings.data_dir)
        self.pt_cache = self.data_dir / "pt_cache"
        self.manifest_path = self.data_dir / "voices_manifest.json"
        self.groups = {
            "v1.0": {
                "repo": settings.repo_v1_0,
                "bin": self.data_dir / "voices-v1.0.bin",
                "seed": Path(settings.seed_bin_v1_0),
                "voices_md": f"{settings.repo_v1_0}/raw/main/VOICES.md",
            },
            "v1.1-zh": {
                "repo": settings.repo_v1_1,
                "bin": self.data_dir / "voices-v1.1-zh.bin",
                "seed": Path(settings.seed_bin_v1_1),
                "voices_md": None,  # official VOICES.md has no numbered zh voices
            },
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pt_cache.mkdir(parents=True, exist_ok=True)

    # ---------- manifest ----------

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                log.warning("voices_manifest.json 损坏,重建")
        return {}

    def _save_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- seed / loading ----------

    def ensure_seed(self) -> None:
        """Copy release seed bins into DATA_DIR on first boot."""
        for name, group in self.groups.items():
            if not group["bin"].exists():
                if group["seed"].exists():
                    shutil.copyfile(group["seed"], group["bin"])
                    log.info("音色种子已就位: %s <- %s", group["bin"], group["seed"])
                else:
                    log.warning("缺少音色种子 %s(组 %s)", group["seed"], name)

    def bin_path(self, group: str) -> Path:
        return self.groups[group]["bin"]

    def load_group(self, group: str) -> dict[str, np.ndarray]:
        """Load a merged group bin as {name: ndarray}. Raises if missing."""
        path = self.bin_path(group)
        if not path.exists():
            raise FileNotFoundError(f"音色包不存在: {path}")
        with np.load(path) as npz:
            return {k: np.asarray(npz[k], dtype=np.float32) for k in npz.files}

    def list_voices(self, group: str | None = None) -> list[dict]:
        manifest = self._load_manifest()
        out = []
        groups = [group] if group else list(self.groups)
        for g in groups:
            for name, meta in sorted((manifest.get(g) or {}).items()):
                out.append({"model": g, "id": name, **meta})
        return out

    # ---------- official sync ----------

    _VOICE_ROW = re.compile(r"^\|\s*(\S+)\s*\|((?:[^|]*\|)+)")

    def _parse_voices_md(self, markdown: str) -> dict[str, dict]:
        """Parse the official VOICES.md table into per-voice metadata.

        Handles both table layouts: the 5-column quality table (Name |
        Traits | Target Quality | Training Duration | Overall Grade) and the
        2-column simple tables (Name | Traits) used for es/fr/pt/es sections.
        Names may be bolded with escaped underscores (``**af\\_heart**``).
        """
        result = {}
        for line in markdown.splitlines():
            match = self._VOICE_ROW.match(line.strip())
            if not match:
                continue
            raw_name, rest = match.group(1), match.group(2)
            candidate = raw_name.strip("*").replace("\\_", "_")
            if not re.fullmatch(r"[a-z]{2}_[a-z0-9_]+", candidate):
                continue
            cells = [c.strip() for c in rest.split("|")]
            cells = [c for c in cells if c and not re.fullmatch(r"`[0-9a-f]{8}`", c)]
            traits = cells[0] if cells else ""
            target_q = duration = overall = None
            if len(cells) >= 4:
                target_q, duration, overall = strip_md(cells[1]), strip_md(cells[2]), strip_md(cells[3])
            gender = ("female" if "🚺" in traits else "male" if "🚹" in traits else "")
            marks = [m for m in ("🔥", "💎", "❤️", "⚡", "🌱") if m in traits]
            result[candidate] = {
                "gender": gender,
                "target_quality": target_q,
                "training_duration": duration,
                "grade": overall,
                "marks": marks,
            }
        return result

    def _fetch_voices_md(self, url: str) -> str | None:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "kokoro-tts/1.0"})
            with urllib.request.urlopen(request, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - metadata is best-effort
            log.warning("VOICES.md 获取失败(%s): %s", url, exc)
            return None

    def _hf_api(self, url: str, timeout: int = 60):
        request = urllib.request.Request(url, headers={"User-Agent": "kokoro-tts/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def _hf_download(self, url: str, dest: Path, tries: int = 4) -> None:
        last_error: Exception | None = None
        for attempt in range(1, tries + 1):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "kokoro-tts/1.0"})
                tmp = dest.with_suffix(dest.suffix + ".part")
                with urllib.request.urlopen(request, timeout=120) as resp, open(tmp, "wb") as fp:
                    shutil.copyfileobj(resp, fp)
                tmp.replace(dest)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(2 * attempt)
        raise RuntimeError(f"下载失败 {url}: {last_error}") from last_error

    def _list_official_voices(self, repo: str) -> list[dict]:
        base = self._s.hf_endpoint
        data = self._hf_api(f"{base}/api/models/{repo}/tree/main/voices")
        return [f for f in data if f.get("path", "").endswith(".pt")]

    def sync(self, groups: list[str] | None = None) -> dict:
        """Pull the latest official voices and merge them into group bins."""
        wanted = groups or list(self.groups)
        manifest = self._load_manifest()
        summary: dict = {}
        for group in wanted:
            if group not in self.groups:
                raise ValueError(f"未知音色组 '{group}',可用: {list(self.groups)}")
            info = self.groups[group]
            repo = info["repo"]
            base = self._s.hf_endpoint
            try:
                remote = self._list_official_voices(repo)
            except Exception as exc:  # noqa: BLE001
                summary[group] = {"error": f"列取官方音色失败: {exc}"}
                continue

            local = manifest.setdefault(group, {})
            existing = {}
            if info["bin"].exists():
                with np.load(info["bin"]) as npz:
                    existing = {k: np.asarray(npz[k], dtype=np.float32) for k in npz.files}

            added, updated, failed = [], [], []
            changed = False
            official_meta = {}
            if info.get("voices_md"):
                markdown = self._fetch_voices_md(f"{base}/{info['voices_md']}")
                if markdown:
                    official_meta = self._parse_voices_md(markdown)
                    log.info("VOICES.md 解析: %d 个音色元数据(%s)", len(official_meta), group)

            for entry in remote:
                path = entry["path"]
                name = path.split("/")[-1].rsplit(".", 1)[0]
                size = int(entry.get("size") or 0)
                meta = local.get(name)
                needs_meta = not meta or (
                    not meta.get("grade") and official_meta.get(name, {}).get("grade")
                ) or (not meta.get("note") and name in self.KNOWN_TRAITS)
                if meta and meta.get("size") == size and name in existing and not needs_meta:
                    continue
                dest = self.pt_cache / f"{group}__{name}.pt"
                try:
                    if not (dest.exists() and dest.stat().st_size == size and size > 0):
                        self._hf_download(f"{base}/{repo}/resolve/main/{path}", dest)
                    tensor = load_pt_tensor(dest)
                    arr = np.asarray(tensor, dtype=np.float32)
                    if arr.ndim == 1:  # some exports store a single style row
                        arr = arr.reshape(1, -1)
                    existing[name] = arr
                    gender = "female" if name[1:2] == "f" else ("male" if name[1:2] == "m" else "")
                    local[name] = {
                        "language": VOICE_LANGUAGE_BY_PREFIX.get(name[:1].lower(), ""),
                        "gender": official_meta.get(name, {}).get("gender") or gender,
                        "size": size,
                        "shape": list(arr.shape),
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": repo,
                    }
                    if name in official_meta:
                        local[name].update(official_meta[name])
                    if name in self.KNOWN_TRAITS:
                        local[name].update(self.KNOWN_TRAITS[name])
                    (added if not meta else updated).append(name)
                    changed = True
                except Exception as exc:  # noqa: BLE001
                    failed.append({"voice": name, "error": str(exc)[:120]})

            # Enrich voices that exist locally but lack (or hold stale) metadata
            for name in existing:
                entry_meta = local.get(name)
                if entry_meta is None:
                    entry_meta = local[name] = {
                        "language": VOICE_LANGUAGE_BY_PREFIX.get(name[:1].lower(), ""),
                        "gender": "female" if name[1:2] == "f" else ("male" if name[1:2] == "m" else ""),
                    }
                want = official_meta.get(name)
                if want:
                    if entry_meta.get("grade") != want.get("grade"):
                        entry_meta.update(want)
                        changed = True
                if "note" not in entry_meta and name in self.KNOWN_TRAITS:
                    entry_meta.update(self.KNOWN_TRAITS[name])
                    changed = True

            if changed:
                ordered = {k: existing[k] for k in sorted(existing)}
                buf = io.BytesIO()
                np.savez(buf, **ordered)
                tmp = info["bin"].with_suffix(".bin.tmp")
                tmp.write_bytes(buf.getvalue())
                tmp.replace(info["bin"])
                log.info("音色组 %s 更新: +%d/~%d, 共 %d", group, len(added), len(updated), len(ordered))

            summary[group] = {
                "official_total": len(remote),
                "local_total": len(existing),
                "added": added,
                "updated": updated,
                "failed": failed,
                "changed": changed,
            }

        self._save_manifest(manifest)
        return summary
