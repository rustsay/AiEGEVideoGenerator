"""Генерация HTML-«видео» через OpenAI-совместимый API.

Заточено под небольшие модели (Gemma ~26B): модель отвечает ТОЛЬКО за
содержимое SVG-доски, массив steps и блок narration. JS-движок плеера
собирается на нашей стороне (_assemble) — поэтому ошибки модели в коде
не ломают воспроизведение. Результат валидируется; при ошибке — до
MAX_ATTEMPTS повторов с передачей списка ошибок модели.
"""
import json
import os
import re

from openai import OpenAI

from .engine import build_player_js

MAX_ATTEMPTS = 3
CONNECT_TIMEOUT = 15.0     # соединение
REQUEST_TIMEOUT = 420.0    # потолок одной попытки
STALL_TIMEOUT = 90.0       # тишина в потоке дольше этого = считаем зависанием

SYSTEM_PROMPT = """Ты — репетитор ЕГЭ по профильной математике, готовишь пошаговое видео-решение задачи в виде одного HTML-файла.

Ты заполняешь ГОТОВЫЙ ШАБЛОН (ниже). Разрешено менять ТОЛЬКО:
1) <title>, <h1> — короткое название задачи;
2) .subtitle — условие своими словами;
3) SVG-группы <g id="stepN" class="fade">…</g> на доске (6–8 групп);
4) содержимое <script id="narration" type="application/json"> — список шагов для озвучки.
ВСЁ ОСТАЛЬНОЕ (стили, прогресс-бар, пустой <script> перед </body>) оставь ровно как в шаблоне.

СООТВЕТСТВИЕ ШАГОВ:
 - одна группа на доске == один элемент narration; групп и записей одинаковое число (6–8);
 - порядок групп step1, step2, … совпадает с порядком narration;
 - последний шаг — «Ответ»: зелёная панель по центру (x=350 y=290 width=500 height=140 rx=16 fill="#f0fdf4" stroke="#86efac") с крупным ответом font-size="40" font-weight="700";
 - первый шаг — условие задачи крупно (заголовок font-size="30", формула font-size="36").

ЧТО НА ДОСКЕ (viewBox 1200×700, фон белый):
 - Панели-подложки с выкладками: голубая fill="#eff6ff" stroke="#93c5fd"; оранжевая fill="#fff7ed" stroke="#fdba74"; сиреневая fill="#f5f3ff" stroke="#c4b5fd"; зелёная fill="#f0fdf4" stroke="#86efac". Всегда rx="16" stroke-width="2".
 - Внутри панели: заголовок text font-size="23" font-weight="700", выкладки font-size="26".
 - Координаты продумай: панели НЕ должны пересекаться. Колонка слева x=65..560, справа x=640..1135, нижний ряд y=500+. Высота доски 700.
 - Геометрические задачи: чертёж линиями class="path" stroke="#202633" stroke-width="4" fill="none"; на каждом шаге добавляй цветную подсветку рассматриваемого элемента (та же линия stroke-width="6" ярким цветом), дуги углов path вида M x y A r r 0 0 1 … , штрихи равных отрезков, подписи длин text font-size="24".
 - Алгебраические: преобразуемая часть выражения подчёркивается цветной линией, ОДЗ выносится в свою панель, корни обводятся окружностью, рисуется числовая прямая с закрашенными/выколотыми точками.
 - Записи и подсветка появляются одновременно и про одно и то же.

МАТЕМАТИКА СТРОГО ВЕРНАЯ: ОДЗ, обоснованные переходы, проверка корней, явный ответ.
Спецсимволы юникодом прямо в тексте: √ ² ³ ₁ ₂ · ⟂ ∠ ⇒ ∈ ≤ ≠.
Внутри текста SVG нельзя использовать символы < > & — замени на слова либо &lt; &gt; &amp;. Кавычки " внутри атрибутов запрещены.
text в narration — связная речь репетитора для озвучки, 15–60 слов на шаг.

===ШАБЛОН===
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Короткое название</title>
<style>
:root{--bg:#eef1f5;--paper:#fff;--ink:#202633;--muted:#667085;--blue:#2563eb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif}
.app{max-width:1200px;margin:18px auto;padding:0 14px}
header{background:var(--paper);border-radius:18px;padding:18px 22px;box-shadow:0 6px 24px #00000012}
h1{margin:0 0 6px;font-size:26px}
.subtitle{color:var(--muted);font-size:16px;line-height:1.45}
.board{margin-top:14px;background:var(--paper);border-radius:18px;overflow:hidden;box-shadow:0 8px 30px #00000014}
svg{display:block;width:100%;height:auto}
.fade{opacity:0;transition:opacity .5s ease}
.fade.on{opacity:1}
.path{stroke-dasharray:1400;stroke-dashoffset:1400}
.path.on{stroke-dashoffset:0;transition:stroke-dashoffset 1s ease}
.pulse{animation:pulse 1s ease 2}
@keyframes pulse{50%{opacity:.28}}
.progress{height:7px;background:#dfe3e8;border-radius:99px;overflow:hidden;margin-top:12px}
.progress>div{height:100%;width:0;background:var(--blue);transition:width .35s}
.status{display:block;text-align:center;margin-top:8px;font-weight:700;font-size:15px;color:var(--muted)}
</style>
</head>
<body>
<div class="app">
<header>
  <h1>Название</h1>
  <div class="subtitle">Условие своими словами.</div>
</header>

<div class="board">
<svg id="boardSvg" viewBox="0 0 1200 700" xmlns="http://www.w3.org/2000/svg">
<rect width="1200" height="700" fill="#ffffff"/>
<g opacity=".05" stroke="#64748b"><path d="M0 50H1200M0 100H1200M0 150H1200M0 200H1200M0 250H1200M0 300H1200M0 350H1200M0 400H1200M0 450H1200M0 500H1200M0 550H1200M0 600H1200M0 650H1200"/><path d="M50 0V700M100 0V700M150 0V700M200 0V700M250 0V700M300 0V700M350 0V700M400 0V700M450 0V700M500 0V700M550 0V700M600 0V700M650 0V700M700 0V700M750 0V700M800 0V700M850 0V700M900 0V700M950 0V700M1000 0V700M1050 0V700M1100 0V700M1150 0V700"/></g>

<g id="step1" class="fade">
  <text x="80" y="90" font-size="30" font-weight="700">Заголовок задачи</text>
  <text x="80" y="150" font-size="36">условие формулой</text>
</g>

<g id="step2" class="fade">
  <rect x="65" y="255" width="500" height="190" rx="16" fill="#fff7ed" stroke="#fdba74" stroke-width="2"/>
  <text x="90" y="292" font-size="23" font-weight="700" fill="#c2410c">1. ОДЗ</text>
  <text x="90" y="335" font-size="27">выкладки…</text>
</g>

<!-- дальше группы step3, step4, step5, step6 (при необходимости до step8) -->

</svg>
</div>

<div class="progress"><div id="bar"></div></div>
<span id="status" class="status"></span>

<script id="narration" type="application/json">
[{"title":"Заголовок шага","text":"Что говорит репетитор на этом шаге."}]
</script>

<script></script>
</body>
</html>
===КОНЕЦ ШАБЛОНА===

ЖЁСТКИЕ ПРАВИЛА ВЫВОДА:
1. Выведи ТОЛЬКО полный HTML-файл: первая строка <!doctype html>, последняя </html>. Никакого markdown и пояснений.
2. Не пиши НИКАКОГО JavaScript: тег <script> без атрибутов оставь ПУСТЫМ, как в шаблоне.
3. Число групп на доске == числу объектов в narration (6–8).
4. JSON в narration — строго валидный: двойные кавычки, никаких запятых в конце, внутри строк не используй кавычки ".
5. Файл должен быть завершён полностью: …</script></body></html>."""

_client = None


def _make_timeout():
    """Timeout для SDK; окружения различаются (httpx / httpx2 / число)."""
    for mod in ("httpx", "httpx2"):
        try:
            m = __import__(mod)
            return m.Timeout(REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT)
        except ImportError:
            continue
    return REQUEST_TIMEOUT


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["LLM_BASE_URL"].rstrip("/"),
            api_key=os.environ["LLM_API_KEY"],
            timeout=_make_timeout(),
            max_retries=0,
        )
    return _client


def _chat_once(client: OpenAI, model: str, messages: list, temperature: float,
               log) -> str:
    """Одна попытка: стримим ответ, сторож рвёт поток при тишине."""
    import threading
    import time

    stream = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature,
        max_tokens=16000, stream=True,
    )
    state = {"last": time.time(), "stalled": False}
    parts: list[str] = []

    def watchdog():
        while not state["stalled"]:
            time.sleep(5)
            if time.time() - state["last"] > STALL_TIMEOUT:
                state["stalled"] = True
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
                return

    threading.Thread(target=watchdog, daemon=True).start()
    err: Exception | None = None
    try:
        for chunk in stream:
            if state["stalled"]:
                break
            state["last"] = time.time()
            if chunk.choices:
                delta = getattr(chunk.choices[0].delta, "content", None)
                if delta:
                    parts.append(delta)
                    total = sum(len(p) for p in parts)
                    if total and total % 4000 < len(delta):
                        log(f"модель печатает… {total // 1000} тыс. символов")
    except Exception as e:  # noqa: BLE001
        err = e

    text = "".join(parts).strip()
    if state["stalled"]:
        raise TimeoutError(f"поток от провайдера завис ({STALL_TIMEOUT:.0f} c без данных)")
    if err and not text:
        raise err
    if not text:
        raise RuntimeError(f"пустой ответ модели: {err or 'без данных'}")
    return text


def _strip_to_html(text: str) -> str:
    text = text.strip()
    m = re.search(r"(<!doctype\s+html.*)|(<html[\s>].*)", text, re.I | re.S)
    if m:
        text = m.group(0)
    text = re.sub(r"^```(?:html)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _parse_json_loose(raw: str):
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(re.sub(r",\s*([\]}])", r"\1", raw))
    except json.JSONDecodeError:
        return None


def _extract_narration(html: str) -> list | None:
    m = re.search(r'<script[^>]*id="narration"[^>]*>(.*?)</script>', html, re.S | re.I)
    if not m:
        return None
    data = _parse_json_loose(m.group(1))
    if isinstance(data, list) and data:
        out = []
        for s in data:
            if not isinstance(s, dict):
                return None
            t = str(s.get("title", "")).strip()
            x = str(s.get("text", "")).strip()
            if not x:
                return None
            out.append({"title": t, "text": x})
        return out
    return None


def _extract_steps(html: str) -> list | None:
    m = re.search(r"const\s+steps\s*=\s*(\[.*?\]);", html, re.S)
    if not m:
        return None
    data = _parse_json_loose(m.group(1))
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return data
    return None


def _ordered_group_ids(html: str) -> list[str]:
    """id групп на доске в порядке появления в документе."""
    return list(dict.fromkeys(re.findall(r'id="(step\d+)"', html)))


def _repair_truncated(html: str) -> str:
    """Модели иногда не влезают в лимит токенов: чиним хвост файла."""
    if "</html>" in html.lower():
        return html
    # отрезать незакрытый тег в конце
    cut = html.rstrip().rfind(">")
    if cut != -1:
        html = html[: cut + 1]
    low = html.lower()
    if "</body>" not in low:
        html += "\n</body>"
    if "</html>" not in html.lower():
        html += "\n</html>"
    return html


def build_steps(html: str) -> tuple[list, list[str]]:
    """Сшивает группы доски и наррацию.

    Небольшие модели часто дают разное число групп и записей — вместо фейла
    лишнее приклеивается к соседнему шагу. Возвращает (steps, ошибки).
    """
    errs = []
    narration = _extract_narration(html)
    src = narration
    if src is None:
        js_steps = _extract_steps(html)
        if js_steps:
            src = [{"title": s.get("title", ""), "text": s.get("text", ""),
                    **({"show": s["show"]} if s.get("show") else {})}
                   for s in js_steps]
    ids = _ordered_group_ids(html)
    if "<!doctype html" not in html.lower() and "<html" not in html.lower():
        return [], ["ответ не является HTML-документом"]
    if not ids:
        errs.append('на доске нет групп <g id="stepN">')
    if not src:
        errs.append('нет блока <script id="narration"> с JSON-массивом {"title","text"}')
    if errs:
        return [], errs

    recs = []
    for s in src:
        t = str(s.get("title", "")).strip()
        x = re.sub(r"\s+", " ", str(s.get("text", ""))).strip()
        if not t and not x:
            continue
        recs.append({"title": t or x[:60], "text": x,
                     **({"show": list(s["show"])} if s.get("show") else {})})
    if len(recs) < 3:
        errs.append(f"слишком мало шагов: {len(recs)} (нужно 6–8)")
        return [], errs

    K, L = len(ids), len(recs)
    steps: list[dict] = []
    if K >= L:
        for i, r in enumerate(recs):
            show = r.get("show") or [ids[i]]
            steps.append({"title": r["title"], "text": r["text"], "show": show})
        if K > L:  # лишние группы — в последний шаг
            steps[-1]["show"] += ids[L:]
    else:  # лишняя наррация — текст добавляется к последнему шагу
        for i in range(K - 1):
            show = recs[i].get("show") or [ids[i]]
            steps.append({"title": recs[i]["title"], "text": recs[i]["text"], "show": show})
        tail = recs[K - 1:]
        steps.append({
            "title": tail[0]["title"],
            "text": " ".join(t["text"] for t in tail if t["text"]),
            "show": recs[K - 1].get("show") or [ids[K - 1]],
        })
    # показать только существующие id
    id_set = set(ids)
    for s in steps:
        s["show"] = [i for i in s["show"] if i in id_set] or [ids[-1]]
    return steps, []


def _assemble(html: str, narration: list[dict]) -> str:
    """Вырезаем пользовательские скрипты, вставляем наш плеер."""
    html = re.sub(r"(?i)<script>(.*?)</script>", "", html, flags=re.S)
    js = "<script>" + build_player_js(narration) + "</script>"
    if "</body>" in html:
        html = html.replace("</body>", js + "\n</body>")
    else:  # недостающие теги дописываем
        html = html.rstrip() + js + "\n</body></html>"
    return html


def generate_video(problem: str, log=lambda m: None) -> dict:
    client = get_client()
    model = os.environ.get("LLM_MODEL", "gpt-4o")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Условие задачи:\n{problem}"},
    ]
    last_err = "нет ответа модели"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _chat_once(client, model, messages,
                             0.25 if attempt == 1 else 0.55, log)
        except (TimeoutError, RuntimeError) as e:
            last_err = str(e)
            log(f"попытка {attempt}: {last_err}")
            continue
        html = _strip_to_html(raw)
        html = _repair_truncated(html)
        steps, errs = build_steps(html)
        if not errs:
            m1 = re.search(r"<h1>(.*?)</h1>", html, re.S)
            m2 = re.search(r"<title>(.*?)</title>", html, re.S)
            raw = m1.group(1) if m1 else (m2.group(1) if m2 else "")
            title = re.sub(r"<[^>]+>", "", raw).strip()[:90] or "Решение задачи"
            if len(_ordered_group_ids(html)) != len(steps):
                log("число групп и шагов разошлось — сшил автоматически")
            log("HTML прошёл проверку")
            return {
                "title": title or "Решение задачи",
                "html": _assemble(html, steps),
                "steps": [{"title": s["title"], "text": s["text"], "dur": 7.0} for s in steps],
            }
        last_err = "; ".join(dict.fromkeys(errs))
        log(f"невалидный ответ: {last_err}")
        messages = messages[:2] + [
            {"role": "assistant", "content": raw[:8000]},
            {"role": "user", "content":
                "Ответ не принят. Ошибки:\n- " + "\n- ".join(dict.fromkeys(errs))
                + "\n\nВыведи заново ПОЛНЫЙ исправленный HTML: с <!doctype html> по </html>, "
                  "пустой <script></script>, без JavaScript, блок narration — JSON-массив "
                  '{"title","text"} по одному на каждую группу stepN.'},
        ]
    raise ValueError(f"Модель не выдала корректный HTML за {MAX_ATTEMPTS} попыток: {last_err}")
