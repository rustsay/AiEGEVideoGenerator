import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, llm, render, tts

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = db.MEDIA_DIR

app = FastAPI(title="ЕГЭ Видео-решения")


@app.on_event("startup")
def _startup() -> None:
    db.init()
    render.check_tools()
    # видео, застрявшие в pending после перезапуска сервера, уже не доделаются
    for v in db.pending_videos():
        db.update_video(v["id"], status="error",
                        error="генерация прервана перезапуском сервера", stage="")


@app.on_event("shutdown")
def _shutdown() -> None:
    pass


class GenerateRequest(BaseModel):
    problem: str
    chat_id: str | None = None


# ---------- чаты ----------

@app.post("/api/chats")
def create_chat():
    return db.create_chat()


@app.get("/api/chats")
def list_chats():
    return db.list_chats()


@app.get("/api/chats/{cid}")
def get_chat(cid: str):
    chat = db.get_chat(cid)
    if not chat:
        raise HTTPException(404, "Чат не найден")
    return chat


# ---------- генерация ----------

def _set_stage(vid: str, stage: str) -> None:
    db.update_video(vid, stage=stage)


def _background_generate(vid: str, problem: str, chat_id: str) -> None:
    workdir = MEDIA_DIR / f"tmp_{vid}"
    try:
        # 1. Решение от модели (с валидацией и ретраями внутри)
        data = llm.generate_video(problem, log=lambda m: _set_stage(vid, m))
        db.update_video(vid, title=data["title"], html=data["html"],
                        steps_json=_dumps(data["steps"]), stage="решение готово")

        # 2. Озвучка по шагам
        _set_stage(vid, "озвучиваю шаги")
        segments = tts.render_segments(data["steps"], workdir)

        # 3. Сборка MP4: скриншоты страницы + дорожка озвучки
        _set_stage(vid, "собираю видео")
        mp4 = MEDIA_DIR / f"{vid}.mp4"
        total = render.render_mp4(data["html"], data["steps"], segments, mp4, workdir)

        db.update_video(vid, status="ready", stage=f"видео {total:.0f} с готово",
                        error=None)
    except Exception as e:  # noqa: BLE001
        db.update_video(vid, status="error", error=str(e)[:500], stage="")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


@app.post("/api/generate")
def generate(req: GenerateRequest):
    problem = req.problem.strip()
    if not problem:
        raise HTTPException(400, "Пустое условие задачи")

    if req.chat_id:
        chat = db.get_chat(req.chat_id)
        if not chat:
            raise HTTPException(404, "Чат не найден")
    else:
        chat = db.create_chat()

    db.append_item(chat["id"], "user", text=problem)
    db.set_chat_title_if_empty(chat["id"], problem[:70])

    vid = uuid.uuid4().hex[:12]
    db.create_video(vid, problem)
    db.append_item(chat["id"], "video", video_id=vid)  # сразу, чтобы пережило F5
    threading.Thread(target=_background_generate, args=(vid, problem, chat["id"]),
                     daemon=True).start()
    return {"id": vid, "chat_id": chat["id"]}


# ---------- видео ----------

def _load_video(vid: str) -> dict:
    v = db.get_video(vid)
    if not v:
        raise HTTPException(404, "Видео не найдено")
    return v


@app.get("/api/videos/{vid}")
def get_video(vid: str):
    return _load_video(vid)


@app.get("/api/videos/{vid}/html")
def get_html(vid: str):
    html = db.get_html(vid)
    if html is None:
        raise HTTPException(400, "Видео ещё не готово")
    return HTMLResponse(html)


@app.get("/api/videos/{vid}/video")
@app.get("/api/videos/{vid}/mp4")
def get_mp4(vid: str):
    meta = _load_video(vid)
    mp4 = MEDIA_DIR / f"{vid}.mp4"
    if not mp4.exists():
        raise HTTPException(400, "Видео ещё собирается")
    safe = (meta.get("title") or "Решение").replace("/", "_")[:60]
    return FileResponse(mp4, media_type="video/mp4",
                        filename=f"{safe}.mp4", content_disposition_type="inline")


@app.get("/api/videos/{vid}/mp3")
def get_mp3(vid: str):
    meta = _load_video(vid)
    if meta["status"] != "ready":
        raise HTTPException(400, "Видео ещё не готово")
    mp3 = MEDIA_DIR / f"{vid}.mp3"
    if not mp3.exists():
        steps = db.get_steps(vid) or []
        tts.render_mp3(steps, meta.get("title") or "Решение", str(mp3))
    safe = (meta.get("title") or "Решение").replace("/", "_")[:60]
    return FileResponse(mp3, media_type="audio/mpeg", filename=f"{safe}.mp3")


app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")
