"""Сборка настоящего MP4 из HTML-решения.

Playwright открывает страницу в headless Chromium, переключает шаги через
window.__engine, делает скриншоты. Затем ffmpeg: скриншоты -> видео по
длительностям сегментов озвучки, сегменты TTS -> единая дорожка, склейка.
"""
import json
import shutil
import subprocess
from pathlib import Path

VIEWPORT = {"width": 1440, "height": 810}
SETTLE_MS = 1300          # ожидание fade/pulse-анимаций после смены шага
TAIL_SEC = 0.9            # тишина после конца голоса в каждом шаге
MIN_STEP_SEC = 3.0

_RECORD_CSS = """
.controls,.info,.hint{display:none!important}
body{overflow:hidden}
.app{max-width:none!important;margin:0!important;padding:10px 22px 0!important}
header{border-radius:12px;padding:12px 20px!important;box-shadow:none!important;border:1px solid #e5e7eb}
h1{font-size:21px!important;margin-bottom:4px!important}
.subtitle{font-size:13px!important;line-height:1.35!important;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.board{margin-top:10px!important;border-radius:12px!important;box-shadow:none!important;border:1px solid #e5e7eb}
#boardSvg{height:calc(100vh - 168px)!important;width:100%!important}
.progress{margin-top:8px!important;height:6px!important;border-radius:99px}
.status{text-align:center;margin-top:6px;font-size:13px!important}
"""


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {p.stderr[-800:]}")


def _pad_segment(src: str, dst: Path, dur: float) -> None:
    """Сегмент озвучки, добитый тишиной до dur секунд (wav)."""
    _run([
        "ffmpeg", "-y", "-i", src,
        "-af", f"apad=whole_dur={dur:.3f}",
        "-ar", "44100", "-ac", "2",
        str(dst),
    ])


def render_mp4(html: str, steps: list[dict], segments: list[dict],
               out_path: str | Path, workdir: str | Path) -> float:
    """Рендерит MP4; возвращает длительность в секундах.

    steps — метаданные шагов (title/text), segments — [{path,dur}] от tts.
    """
    from playwright.sync_api import sync_playwright

    workdir = Path(workdir).resolve()
    out_path = Path(out_path).resolve()
    frames_dir = workdir / "frames"
    audio_dir = workdir / "audio"
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    html_path = workdir / "page.html"
    html_path.write_text(html, encoding="utf-8")

    n = len(steps)

    # --- кадры ---
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-device-scale-factor=1"])
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(html_path.as_uri(), wait_until="load")
        page.add_style_tag(content=_RECORD_CSS)
        page.evaluate("window.__engine && window.__engine.stop()")
        page.wait_for_timeout(900)
        for i in range(n):
            page.evaluate(f"window.__engine.goto({i})")
            page.wait_for_timeout(SETTLE_MS if i else SETTLE_MS + 400)
            page.screenshot(path=str(frames_dir / f"f_{i:02d}.png"))
        page.close()
        browser.close()

    # --- длительности шагов = голос + хвост ---
    durs = [max(MIN_STEP_SEC, seg["dur"] + TAIL_SEC) for seg in segments]

    # --- аудиодорожка ---
    padded = []
    for i, seg in enumerate(segments):
        p = audio_dir / f"a_{i:02d}.wav"
        _pad_segment(seg["path"], p, durs[i])
        padded.append(p)
    concat_audio = audio_dir / "list.txt"
    concat_audio.write_text("".join(f"file '{p}'\n" for p in padded), encoding="utf-8")
    voice_wav = audio_dir / "voice.wav"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_audio),
          "-c", "copy", str(voice_wav)])

    # --- видеоряд ---
    concat_frames = frames_dir / "list.txt"
    lines = ["ffconcat version 1.0\n"]
    for i, d in enumerate(durs):
        lines.append(f"file 'f_{i:02d}.png'\nduration {d:.3f}\n")
    lines.append("file 'f_%02d.png'\n" % (n - 1))  # фикс последнего кадра
    concat_frames.write_text("".join(lines), encoding="utf-8")

    tmp_out = workdir / "out.mp4"
    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_frames),
        "-i", str(voice_wav),
        "-vf", "fps=30,format=yuv420p,scale=1440:810",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-shortest",
        str(tmp_out),
    ])
    shutil.move(str(tmp_out), str(out_path))
    return sum(durs)


def check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} не найден в PATH")


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])
