#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Веб-путь пользователя: инициатива → сегменты → панель → допущения → расчёт.

Главное архитектурное правило: **приложение ничего не считает само.** Оно собирает конфиг,
отдаёт его движку и показывает то, что движок вернул. Возрастные ограничения, индекс
уверенности, вердикт, флаги — всё приходит из `src/calc.py`. Как только здесь появится
своя арифметика, у проекта станет три модели вместо двух, и они разойдутся.

Запуск: make web   (или .venv/bin/uvicorn web.app:app --reload)
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


calc = _load("calc")
params = _load("params")

# Версия движка = хэш его исходника. Расчёт хранит её, чтобы через полгода было видно,
# той ли моделью посчитана цифра, которую показали комитету.
ENGINE_VERSION = hashlib.sha256((ROOT / "src" / "calc.py").read_bytes()).hexdigest()[:12]

# Типы инициатив и продукты живут в реестре: от них зависит и форма, и подсказки навыка
INITIATIVE_TYPES = params.TYPE_CHOICES
PRODUCTS = params.PRODUCTS
PRODUCT_NAME = params.PRODUCT_NAME
STEPS = [("segments", "Сегменты"), ("panel", "Панель"), ("assumptions", "Допущения"),
         ("result", "Результат")]

app = FastAPI(title="Калькулятор эффекта инициатив")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=str(HERE / "templates"))
con = db.connect()


# ------------------------------------------------------------------ форматирование
def _fmt_num(v, digits=0):
    if v is None or v == "":
        return "—"
    try:
        s = f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)
    return s.replace(",", " ").replace(".", ",")


tpl.env.filters["num"] = lambda v, d=0: _fmt_num(v, d)
tpl.env.filters["money"] = lambda v: "—" if v is None else _fmt_num(v) + " ₽"
tpl.env.filters["mln"] = lambda v: "—" if v is None else _fmt_num(float(v) / 1e6, 1) + " млн ₽"
tpl.env.filters["pct"] = lambda v, d=1: "—" if v is None else _fmt_num(float(v) * 100, d) + "%"
tpl.env.filters["from_json"] = lambda s: json.loads(s or "{}")
tpl.env.globals.update(params=params, calc=calc, SEGMENTS=calc.SEGMENTS,
                       TYPES=INITIATIVE_TYPES, PRODUCTS=PRODUCTS, PRODUCT_NAME=PRODUCT_NAME,
                       STEPS=STEPS, ENGINE_VERSION=ENGINE_VERSION,
                       TYPE_META=params.INITIATIVE_TYPES)


def author_of(request):
    """Имя из куки. Куки ходят в latin-1, поэтому русское имя хранится процентным кодом."""
    return unquote(request.cookies.get("author", ""))


def page(request, name, **ctx):
    ctx.setdefault("author", author_of(request))
    return tpl.TemplateResponse(request, name, ctx)


def _num(raw, default=0.0):
    """Число из формы. Русская запятая как разделитель — норма, а не ошибка ввода."""
    if raw is None:
        return default
    s = str(raw).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


# ------------------------------------------------------------------ сборка конфига
def form_to_config(form, cfg):
    """Переносит поля формы в конфиг по реестру. Ни одной подписи и ни одного ключа руками."""
    for sec, _, ps in params.SECTIONS:
        if sec == "meta":
            continue
        for p in ps:
            field = f"{sec}.{p.key}"
            if field in form:
                cfg.setdefault(sec, {})[p.key] = _num(form[field], p.default)

    # Сегменты. Набор задаётся галочками на шаге «Сегменты», а посегментные переопределения
    # правятся на шаге «Допущения» — там галочек нет. Поэтому трогаем только то, что реально
    # пришло в ЭТОЙ форме: иначе второй шаг молча стирал бы выбор, сделанный на первом.
    cfg.setdefault("audience", {})
    existing = {s["id"]: dict(s) for s in (cfg["audience"].get("segments") or [])
                if s.get("id") in calc.SEGMENTS}
    if "segments" in form:
        chosen = [s for s in form.getlist("segments") if s in calc.SEGMENTS]
    else:
        chosen = list(existing)

    segs = []
    for sid in chosen:
        s = existing.get(sid) or {}
        s["id"] = sid
        for key, default in ([(p.key, p.default) for p in params.SEGMENT_SCOPED]
                             + [("reachable_share", None)]):
            field = f"seg.{sid}.{key}"
            if field not in form:
                continue                       # поля нет в этой форме — значение не трогаем
            raw = form.get(field)
            if raw in (None, ""):
                s.pop(key, None)               # очищено — значит «брать общее значение»
            else:
                s[key] = _num(raw, default or 0)
        segs.append(s)

    if segs:
        cfg["audience"]["segments"] = segs
    else:
        cfg["audience"].pop("segments", None)

    # каналы: строки таблицы, пустые пропускаем
    if any(k.startswith("ch.") for k in form.keys()):
        chans, i = [], 0
        while any(k.startswith(f"ch.{i}.") for k in form.keys()):
            name = (form.get(f"ch.{i}.name") or "").strip()
            if name:
                ch = {"name": name, "kind": form.get(f"ch.{i}.kind") or "perform"}
                for p in params.CHANNELS:
                    if p.key in ("name", "kind"):
                        continue
                    raw = form.get(f"ch.{i}.{p.key}")
                    if raw not in (None, ""):
                        val = _num(raw, p.default)
                        if p.key == "frk_tier":
                            val = int(val)
                        if val or p.key == "frk_tier":
                            ch[p.key] = val
                chans.append(ch)
            i += 1
        cfg["channels"] = chans
    return cfg


def config_of(init):
    cfg = json.loads(init["config_json"] or "{}")
    base = params.defaults()
    for sec in base:
        if sec == "channels":
            cfg.setdefault("channels", [])
        else:
            merged = dict(base[sec]); merged.update(cfg.get(sec) or {})
            cfg[sec] = merged
    cfg["meta"]["name"] = init["name"]
    cfg["meta"]["type"] = init["type"]
    cfg["meta"]["product"] = init["product"]
    return cfg


def resolved(cfg):
    """Прогон через движок: строгая валидация, затем сведение сегментов."""
    return calc.resolve_segments(calc.validate_config(json.loads(json.dumps(cfg))))


# ------------------------------------------------------------------ список
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return page(request, "index.html", items=db.list_initiatives(con),
                calcs=db.list_calculations(con)[:15])


@app.post("/author")
def set_author(request: Request, author: str = Form(""), back: str = Form("/")):
    r = RedirectResponse(back, status_code=303)
    r.set_cookie("author", quote(author.strip()), max_age=60 * 60 * 24 * 365)
    return r


# ------------------------------------------------------------------ шаг 1: инициатива
@app.get("/initiative/new", response_class=HTMLResponse)
def new_form(request: Request):
    return page(request, "new.html")


@app.post("/initiative/new")
async def new_save(request: Request):
    f = await request.form()
    name = (f.get("name") or "").strip() or "Без названия"
    iid = db.create_initiative(
        con, author_of(request), name,
        f.get("type") or "frk4", f.get("product") or "card",
        (f.get("description") or "").strip(), params.defaults())
    return RedirectResponse(f"/initiative/{iid}/segments", status_code=303)


@app.get("/initiative/{iid}/edit", response_class=HTMLResponse)
def edit_form(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    return page(request, "new.html", init=init)


@app.post("/initiative/{iid}/edit")
async def edit_save(request: Request, iid: int):
    f = await request.form()
    db.update_initiative(con, iid, name=(f.get("name") or "").strip() or "Без названия",
                         type=f.get("type") or "frk4", product=f.get("product") or "card",
                         description=(f.get("description") or "").strip())
    return RedirectResponse(f"/initiative/{iid}/segments", status_code=303)


@app.post("/initiative/{iid}/delete")
def delete(iid: int):
    db.delete_initiative(con, iid)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ шаг 2: сегменты
@app.get("/initiative/{iid}/segments", response_class=HTMLResponse)
def segments_form(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    chosen = {s["id"]: s for s in (cfg.get("audience", {}).get("segments") or [])}
    return page(request, "segments.html", init=init, cfg=cfg, chosen=chosen, step="segments")


@app.post("/initiative/{iid}/segments")
async def segments_save(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = form_to_config(await request.form(), config_of(init))
    db.update_initiative(con, iid, config=cfg, status="segments")
    return RedirectResponse(f"/initiative/{iid}/panel", status_code=303)


@app.post("/api/segments/preview")
async def segments_preview(request: Request):
    """Возрастные ограничения — из движка, а не из копии правил в JavaScript."""
    body = await request.json()
    product = body.get("product") or ""
    rows = [{"id": s["id"], "target_size": _num(s.get("target_size"), 0)}
            for s in body.get("segments", []) if s.get("id") in calc.SEGMENTS]
    if not rows:
        return JSONResponse({"rows": [], "notes": []})
    probe = params.defaults()
    probe["meta"]["product"] = product
    probe["audience"]["segments"] = rows
    seg = calc.resolve_segments(probe).get("_segments", {})
    return JSONResponse({
        "rows": [{"id": r["id"], "name": r["name"], "available_share": r["available_share"],
                  "min_age": r["min_age"], "effective_audience": r["effective_audience"]}
                 for r in seg.get("rows", [])],
        "notes": seg.get("notes", []),
    })


# ------------------------------------------------------------------ шаг 3: панель
@app.get("/initiative/{iid}/panel", response_class=HTMLResponse)
def panel_form(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    seg_ids = [s["id"] for s in (cfg.get("audience", {}).get("segments") or [])]
    return page(request, "panel.html", init=init, cfg=cfg, step="panel",
                seg_ids=seg_ids, report=db.latest_panel(con, iid),
                brief=panel_brief(init, cfg, seg_ids))


def panel_brief(init, cfg, seg_ids):
    """Бриф для навыка idea-research-panel — готовый текст, который человек уносит в Claude.

    Панель запускается моделью, а не этим приложением, поэтому мост сделан честно:
    приложение готовит бриф и хранит отчёт, но не делает вид, что запустило исследование.
    Для сегмента kids панель обязана быть родительской: до 14 лет решение принимает
    третье лицо, а не ребёнок.
    """
    names = ", ".join(calc.SEGMENTS[s]["name"] for s in seg_ids) or "не выбраны"
    lines = [
        "Проведи панель качественного исследования по этой инициативе.",
        "",
        f"Инициатива: {init['name']}",
        f"Тип: {dict(INITIATIVE_TYPES).get(init['type'], init['type'])}",
        f"Продукт: {PRODUCT_NAME.get(init['product'], init['product'])}",
        f"Сегменты: {names}",
    ]
    if init["description"]:
        lines += ["", "Описание:", init["description"]]
    if "kids" in seg_ids:
        lines += ["", "Важно: по сегменту «Дети (0–13)» респондент — родитель, а не ребёнок. "
                  "Собери родительские персоны и обязательно комплаенс-голос: идея касается "
                  "детей и денег."]
    lines += ["", "Решение по итогам: запускать инициативу или нет и на какой бюджет.",
              "Верни отчёт по своему стандартному шаблону."]
    return "\n".join(lines)


@app.post("/initiative/{iid}/panel")
async def panel_save(request: Request, iid: int):
    f = await request.form()
    body = (f.get("body") or "").strip()
    if body:
        init = db.get_initiative(con, iid)
        cfg = config_of(init)
        seg_ids = ", ".join(s["id"] for s in (cfg.get("audience", {}).get("segments") or []))
        db.save_panel(con, iid, seg_ids, body)
    db.update_initiative(con, iid, status="panel")
    nxt = "assumptions" if f.get("action") != "stay" else "panel"
    return RedirectResponse(f"/initiative/{iid}/{nxt}", status_code=303)


# ------------------------------------------------------------------ шаг 4: допущения
@app.get("/initiative/{iid}/assumptions", response_class=HTMLResponse)
def assumptions_form(request: Request, iid: int, error: str = ""):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    chosen = {s["id"]: s for s in (cfg.get("audience", {}).get("segments") or [])}
    return page(request, "assumptions.html", init=init, cfg=cfg, chosen=chosen,
                step="assumptions", error=error, conf=calc.confidence_index(cfg))


@app.post("/initiative/{iid}/assumptions")
async def assumptions_save(request: Request, iid: int):
    f = await request.form()
    init = db.get_initiative(con, iid)
    cfg = form_to_config(f, config_of(init))
    db.update_initiative(con, iid, config=cfg, status="assumptions")
    if f.get("action") == "save":
        return RedirectResponse(f"/initiative/{iid}/assumptions", status_code=303)
    return RedirectResponse(f"/initiative/{iid}/calculate", status_code=303)


@app.post("/api/confidence")
async def api_confidence(request: Request):
    """Индекс уверенности считает движок: формула одна и та же и в отчёте, и в книге."""
    body = await request.json()
    counts = {k: int(_num(body.get(k), 0)) for k in "ABCD"}
    return JSONResponse(calc.confidence_index({"confidence": counts}) or
                        {"index": None, "tier": "—", "note": "Проставьте грейды допущений."})


# ------------------------------------------------------------------ расчёт
@app.post("/initiative/{iid}/calculate")
@app.get("/initiative/{iid}/calculate")
def calculate(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    try:
        res = calc.analyze(resolved(cfg))
    except calc.ConfigError as e:
        return RedirectResponse(f"/initiative/{iid}/assumptions?error={str(e)[:900]}",
                                status_code=303)
    cid = db.save_calculation(con, iid, author_of(request),
                              ENGINE_VERSION, cfg, res)
    db.update_initiative(con, iid, status="calculated")
    return RedirectResponse(f"/calculation/{cid}", status_code=303)


@app.get("/calculation/{cid}", response_class=HTMLResponse)
def calculation_page(request: Request, cid: int):
    c = db.get_calculation(con, cid)
    res = json.loads(c["result_json"])
    init = db.get_initiative(con, c["initiative_id"])
    return page(request, "result.html", c=c, res=res, init=init, step="result",
                history=db.list_calculations(con, c["initiative_id"]),
                panel=db.latest_panel(con, c["initiative_id"]))


@app.get("/calculation/{cid}/xlsx")
def calculation_xlsx(cid: int):
    """Живая книга по одной инициативе: формулы, а не значения — её можно крутить самому.

    Собирается из СНИМКА конфига, а не из текущего состояния инициативы: выгрузка обязана
    соответствовать расчёту, на который человек ссылается.
    """
    c = db.get_calculation(con, cid)
    init = db.get_initiative(con, c["initiative_id"])
    tmp = Path(tempfile.mkdtemp())
    case, out = tmp / "case.json", tmp / "model.xlsx"
    case.write_text(c["config_snapshot"], encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "src" / "build_xlsx.py"), str(case), str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        return JSONResponse({"error": "Не удалось собрать книгу", "detail": r.stderr[-800:]},
                            status_code=500)
    safe = "".join(ch for ch in init["name"] if ch.isalnum() or ch in " -_()")[:60].strip()
    return FileResponse(out, filename=f"{safe or 'инициатива'} — расчёт {cid}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument."
                                   "spreadsheetml.sheet")
