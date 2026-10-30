"""Озвучка через edge-tts.

render_segments — отдельный mp3 на каждый шаг (+длительность), из них
собирается дорожка для MP4. render_mp3 — единый файл для скачивания.
"""
import asyncio
import json
import subprocess
from pathlib import Path

import edge_tts

VOICES = ("ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural")
RATE = "-6%"


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _full_text(steps: list[dict], title: str) -> str:
    parts = [f"Видео-решение. {title}."]
    for i, s in enumerate(steps, 1):
        t = f"{s.get('title', '')}. {s.get('text', '')}".strip(". ")
        if t:
            parts.append(f"Шаг {i}. {t}")
    return "\n\n".join(p for p in parts if p.strip())


async def _save(text: str, voice: str, out: str) -> None:
    await edge_tts.Communicate(text, voice, rate=RATE).save(out)


def _synthesize(text: str, out: str) -> None:
    """Синтез с перебором голосов; проверяем, что файл не пустой."""
    last: Exception | None = None
    for voice in VOICES:
        try:
            asyncio.run(_save(text, voice, out))
            if Path(out).exists() and Path(out).stat().st_size > 1024:
                return
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"TTS не вернул аудио: {last or 'пустой поток'}")


def render_segments(steps: list[dict], workdir: str | Path) -> list[dict]:
    """Для каждого шага: seg_XX.mp3 и его длительность (сек).

    Возвращает [{"path": ..., "dur": ...}] — по одному на шаг.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, s in enumerate(steps):
        text = f"{s.get('title', '')}. {s.get('text', '')}".strip(". ").strip()
        if not text:
            text = "Продолжаем решение."
        out = workdir / f"seg_{i:02d}.mp3"
        _synthesize(text, str(out))
        segs.append({"path": str(out), "dur": _probe_duration(str(out))})
    return segs


def render_mp3(steps: list[dict], title: str, out_path: str) -> None:
    """Единая MP3-озвучка всего решения."""
    _synthesize(_full_text(steps, title), out_path)
