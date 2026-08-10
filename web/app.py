#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Веб-путь пользователя: инициатива → сегменты → панель → допущения → расчёт.

Главное архитектурное правило: **приложение ничего не считает само.** Оно собирает конфиг,
отдаёт его движку и показывает то, что движок вернул. Возрастные ограничения, индекс
уверенности, вердикт, флаги — всё приходит из `src/calc.py`. Как только здесь появится
своя арифметика, у проекта станет три модели вместо двух, и они разойдутся.

Запуск: make web   (или .venv/bin/uvicorn web.app:app --reload)
"""
import copy
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
STEPS = [("brief", "Бриф"), ("pilot", "Пилот"), ("segments", "Сегменты"),
         ("panel", "Панель"), ("assumptions", "Допущения"), ("result", "Результат")]

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
# Валюта — аргумент фильтра, а не константа: расчёт в сумах, подписанный рублями,
# выглядит правдоподобно и неверен ровно на курс. По умолчанию рубль,
# чтобы старые шаблоны и кейсы в рублях не менялись.
tpl.env.filters["money"] = lambda v, cur="₽": "—" if v is None else _fmt_num(v) + f" {cur}"
tpl.env.filters["mln"] = lambda v, cur="₽": ("—" if v is None
                                             else _fmt_num(float(v) / 1e6, 1) + f" млн {cur}")
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
    # Название, тип и продукт — колонки таблицы инициатив, их правит шаг «Описание»;
    # `config_of()` подставляет их поверх конфига. Остальное в `meta` — горизонт, ставка,
    # валюта, курс — обычные параметры, и раньше они были недоступны из веба целиком:
    # горизонт застревал на дефолте 24, а вопрос «12 / 24 / 36» есть в первой волне навыка.
    FROM_INITIATIVE = {"name", "type", "product"}
    for sec, _, ps in params.SECTIONS:
        for p in ps:
            if sec == "meta" and p.key in FROM_INITIATIVE:
                continue
            field = f"{sec}.{p.key}"
            if field in form:
                raw = form[field]
                # текстовые параметры (режим экономики) через _num превратились бы в 0.0
                cfg.setdefault(sec, {})[p.key] = (
                    (str(raw).strip() or p.default) if p.fmt == "text" else _num(raw, p.default))

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


def form_to_nested(form, cfg, section):
    """Поля формы → вложенный раздел конфига. Общий разбор для всех таких блоков.

    Бриф и пилот разбираются своими функциями: у них повторяющиеся строки и своя
    механика. У остальных блоков поля плоские, поэтому один разбор на всех —
    иначе четвёртая копия одного и того же кода разойдётся с первыми тремя.

    Пустой блок убирается целиком: полузаполненный `linked_client` без дохода
    связанного даёт нулевой эффект и выглядит как «посчитали и ничего не вышло».
    """
    if not form.get(f"has_{section}"):
        cfg.pop(section, None)
        return cfg
    out = {}
    for p in params.nested_fields(section):
        raw = form.get(f"{section}.{p.key}")
        if raw in (None, ""):
            continue
        out[p.key] = str(raw).strip() if p.fmt == "text" else _num(raw, p.default)
    if out:
        # сохраняем то, что форма не показывает (например sources), — иначе шаг
        # молча стёр бы заданное конфигом
        keep = {k: v for k, v in (cfg.get(section) or {}).items()
                if k not in {p.key for p in params.nested_fields(section)}}
        cfg[section] = {**keep, **out}
    else:
        cfg.pop(section, None)
    return cfg


def apply_product_economics(cfg, product):
    """Режим экономики и горизонт из каталога продукта.

    Вызывается на ДВУХ явных событиях — создание инициативы и смена продукта, — а не
    при каждом сохранении. Причина: `config_of()` заливает конфиг дефолтами реестра,
    поэтому «поле пустое» отличить от «поле равно дефолту» уже нельзя, и подстановка
    при каждом сохранении затирала бы то, что человек поправил руками.
    """
    econ = params.product_economics(product)
    ue = cfg.setdefault("unit_economics", {})
    ue["mode"] = econ["mode"]
    if econ["mode"] == calc.MODE_VALUE:
        # Горизонт и ставка идут из модели продукта: ценность посчитана именно на них,
        # и расхождение сделало бы затраты и доходы несопоставимыми.
        ue["value_horizon_months"] = econ["value_horizon_months"]
        meta = cfg.setdefault("meta", {})
        meta["horizon_months"] = econ["horizon_months"]
        meta["discount_rate_annual"] = econ["discount_rate_annual"]
        # Обнуляем то, что собирало бы маржу снизу вверх: иначе дефолты дебетовой карты
        # остаются в конфиге вклада и движок в каждом прогоне честно предупреждает, что
        # они не участвуют. Средний баланс не трогаем — в этом режиме он больше не даёт
        # маржи, но по-прежнему нужен для AUM.
        for key in ("nim_annual", "monthly_turnover", "interchange_rate", "fee_income_m",
                    "cashback_rate", "servicing_cost_m", "cor_annual"):
            ue[key] = 0
    return cfg


def apply_segment_defaults(cfg, product):
    """Заполняет по каждому сегменту то, что известно из реестра, не трогая введённое.

    Сегменты хранят только явно заданные ключи (`form_to_config` удаляет очищенные),
    поэтому здесь «нет ключа» честно означает «человек не вводил».
    """
    for seg in cfg.get("audience", {}).get("segments") or []:
        d = params.segment_defaults(seg.get("id"), product)
        if not d:
            continue
        for key in ("target_size", "existing_clients", "reachable_share",
                    "value_per_client", "operating_income_annual"):
            if d.get(key) is not None and seg.get(key) in (None, ""):
                seg[key] = d[key]
    return cfg


def prefilled_summary(init, cfg):
    """Что уже подставлено и откуда.

    Показываем, но не спрашиваем: человек должен видеть, на чём считается модель,
    и при этом не заполнять то, что система знает. Источник у каждой строки указан —
    без него «подставлено» неотличимо от «взято с потолка».
    """
    rows, ue, meta = [], cfg.get("unit_economics", {}), cfg.get("meta", {})
    if ue.get("mode") == calc.MODE_VALUE:
        rows.append(("Экономика клиента", "готовая ценность из модели продукта",
                     "каталог продуктов"))
        rows.append(("Горизонт оценки", f'{meta.get("horizon_months", 0):.0f} мес',
                     "модель продукта"))
        rows.append(("Ставка дисконтирования",
                     f'{meta.get("discount_rate_annual", 0) * 100:.0f}%', "модель продукта"))
    else:
        rows.append(("Экономика клиента", "собирается снизу вверх: баланс, ставки, отток",
                     "у продукта нет готовой ценности"))

    for seg in cfg.get("audience", {}).get("segments") or []:
        sid = seg.get("id")
        name = calc.SEGMENTS[sid]["name"] if sid in calc.SEGMENTS else sid
        d = params.segment_defaults(sid, init["product"]) or {}
        own = {k for k in ("target_size", "existing_clients", "value_per_client",
                           "operating_income_annual") if seg.get(k) != d.get(k)}
        pen = d.get("penetration")
        # Форматирует ЧИСЛО, а не строку: `.replace(",", " ")` по всей строке съедает
        # обычные запятые вместе с разделителями разрядов. Чинилось дважды — см. п. 35.
        rows.append((f"{name} — рынок",
                     f'{calc.nf(seg.get("target_size", 0))} чел'
                     + (f' · проникновение {pen:.0%}' if pen else ""),
                     "введено вручную" if "target_size" in own else "реестр"))
        if seg.get("value_per_client"):
            rows.append((f"{name} — ценность клиента",
                         f'{calc.nf(seg["value_per_client"])} ₽',
                         "введено вручную" if "value_per_client" in own else "каталог продуктов"))
        if seg.get("operating_income_annual"):
            rows.append((f"{name} — ОД на клиента",
                         f'{calc.nf(seg["operating_income_annual"])} ₽/год',
                         "введено вручную" if "operating_income_annual" in own
                         else "отчётность по возрастам"))
    return rows


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


# Куда вести человека, если конфиг отвергнут из-за раздела, которого нет на текущем шаге.
# `validate_config` проверяет конфиг ЦЕЛИКОМ, поэтому полузаполненный бриф блокирует
# сохранение пилота — и человек читает на шаге «Пилот» ошибку про бриф, не понимая,
# где чинить. На живом тесте это выглядело как «пилот не применяется ни при каких значениях».
ERROR_STEP = {"brief": ("brief", "Бриф"), "pilot": ("pilot", "Пилот"),
              "audience": ("segments", "Сегменты"), "channels": ("assumptions", "Допущения")}


def split_error(err, current):
    """Делит текст ошибки на «про этот шаг» и «про другие», с адресом чужой части.

    Строку-заголовок («Конфиг не прошёл проверку:») в «мою» часть не тащим: иначе шаг
    показывает «движок не принял пилот» там, где к пилоту нет ни одной претензии.
    """
    mine, foreign = [], {}
    for line in str(err).splitlines():
        hit = next((s for s in ERROR_STEP if s in line), None)
        if hit is None:
            continue                                  # заголовок или общая строка
        if ERROR_STEP[hit][0] == current:
            mine.append(line)
        else:
            foreign.setdefault(hit, []).append(line.strip())
    return "\n".join(mine), foreign


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
    product = f.get("product") or "card"
    iid = db.create_initiative(
        con, author_of(request), name, f.get("type") or "frk4", product,
        (f.get("description") or "").strip(),
        apply_product_economics(params.defaults(), product))
    # Первым делом бриф: описание раскладывается на утверждения ДО всех вопросов.
    # Если отправить человека сразу на сегменты, описание так и останется текстом,
    # который никто не превратил в параметры, — ровно та тихая потеря вводных,
    # ради которой шаг и заведён.
    return RedirectResponse(f"/initiative/{iid}/brief", status_code=303)


@app.get("/initiative/{iid}/edit", response_class=HTMLResponse)
def edit_form(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    return page(request, "new.html", init=init)


@app.post("/initiative/{iid}/edit")
async def edit_save(request: Request, iid: int):
    f = await request.form()
    init = db.get_initiative(con, iid)
    product = f.get("product") or "card"
    fields = dict(name=(f.get("name") or "").strip() or "Без названия",
                  type=f.get("type") or "frk4", product=product,
                  description=(f.get("description") or "").strip())
    if product != init["product"]:
        # продукт сменился — прежняя экономика относилась к другому продукту
        cfg = apply_product_economics(config_of(init), product)
        for seg in cfg.get("audience", {}).get("segments") or []:
            for key in ("target_size", "existing_clients", "reachable_share",
                        "value_per_client", "operating_income_annual"):
                seg.pop(key, None)
        fields["config"] = apply_segment_defaults(cfg, product)
    db.update_initiative(con, iid, **fields)
    return RedirectResponse(f"/initiative/{iid}/segments", status_code=303)


@app.post("/initiative/{iid}/delete")
def delete(iid: int):
    db.delete_initiative(con, iid)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ шаг 2: сегменты
CLAIM_FIELDS = ("claim", "implication", "maps_to", "grade", "status", "reason")


def form_to_parts(form, cfg, section):
    """Строки таблицы частей базы → `cfg[section]["parts"]`.

    Та же механика, что у утверждений брифа: пустые строки выбрасываются, поэтому
    запас пустых строк в форме ничего не стоит. Ключ строки — название части:
    часть без имени нельзя обсуждать, а безымянная доля с потолком ноль читается
    как ошибка ввода.

    Пустой список УДАЛЯЕТСЯ, а не пишется пустым: движок отличает «частей нет,
    считай по всей базе» от «части заданы» — и на первом ставит предупреждение.
    """
    blk = cfg.get(section)
    if not isinstance(blk, dict):
        return cfg
    fields = [f.key for f in params.part_fields(section)]
    parts, i = [], 0
    while any(k.startswith(f"{section}.part.{i}.") for k in form.keys()):
        name = (form.get(f"{section}.part.{i}.name") or "").strip()
        if name:
            pt = {"name": name}
            for key in fields:
                if key == "name":
                    continue
                v = (form.get(f"{section}.part.{i}.{key}") or "").strip()
                if v:
                    pt[key] = _num(v)
            parts.append(pt)
        i += 1
    if parts:
        blk["parts"] = parts
    else:
        blk.pop("parts", None)
    return cfg


def form_to_split(form, cfg, section):
    """Строки разложения оборота → `cfg[section]["turnover_split"]`.

    Здесь не список, а словарь {название: {share, fee}} — движок хранит виды
    операций по имени. Дубликат имени поэтому молча затирал бы предыдущую строку:
    вторую и дальше отмечаем суффиксом, чтобы человек увидел свою ошибку в форме,
    а не потерял долю оборота без следа.
    """
    blk = cfg.get(section)
    if not isinstance(blk, dict):
        return cfg
    keys = [f.key for f in params.split_fields(section) if f.key != "name"]
    split, i = {}, 0
    while any(k.startswith(f"{section}.txn.{i}.") for k in form.keys()):
        name = (form.get(f"{section}.txn.{i}.name") or "").strip()
        if name:
            while name in split:
                name += " (2)"
            split[name] = {k: _num(v) for k in keys
                           if (v := (form.get(f"{section}.txn.{i}.{k}") or "").strip())}
        i += 1
    if split:
        blk["turnover_split"] = split
    else:
        blk.pop("turnover_split", None)
    return cfg


def form_to_seasonality(form, cfg):
    """Двенадцать множителей → `acquisition.seasonality`.

    Профиль пишется только целиком. Заполненные наполовину двенадцать месяцев —
    это не «остальные равны единице», а форма плана, про которую человек думает,
    что задал её: движок такой профиль отвергает, и правильно делает. Поэтому
    частично заполненный набор отправляем ему как есть, не дорисовывая единицы.
    """
    field = "acquisition.season.0"
    if field not in form:
        return cfg                       # этой формы здесь нет — значение не трогаем
    raw = [(form.get(f"acquisition.season.{i}") or "").strip()
           for i in range(params.SEASON_MONTHS)]
    acq = cfg.setdefault("acquisition", {})
    if not any(raw):
        acq.pop("seasonality", None)
    else:
        acq["seasonality"] = [_num(v) for v in raw if v]
    return cfg


def form_to_brief(form, cfg, description):
    """Строки таблицы утверждений → раздел `brief` конфига.

    Пустые строки (без текста утверждения) выбрасываются: человек добавляет их
    заранее, а заполняет не все. Пустые поля не пишутся вовсе, чтобы конфиг
    не зарастал `null`-ами, которые движок потом обязан отличать от нуля.
    """
    claims, i = [], 0
    while any(k.startswith(f"claim.{i}.") for k in form.keys()):
        text = (form.get(f"claim.{i}.claim") or "").strip()
        if text:
            c = {}
            for key in CLAIM_FIELDS:
                v = (form.get(f"claim.{i}.{key}") or "").strip()
                if v:
                    c[key] = v
            claims.append(c)
        i += 1
    if claims or description:
        cfg["brief"] = {"description": description, "claims": claims}
    else:
        cfg.pop("brief", None)
    return cfg


@app.get("/initiative/{iid}/brief", response_class=HTMLResponse)
def brief_form(request: Request, iid: int, error: str = ""):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    brief = cfg.get("brief") or {}
    return page(request, "brief.html", init=init, step="brief", error=error,
                claims=brief.get("claims") or [],
                coverage=calc.brief_coverage(cfg),
                statuses=calc.CLAIM_STATUS, grades=list(calc.GRADE_W))


@app.post("/initiative/{iid}/brief")
async def brief_save(request: Request, iid: int):
    f = await request.form()
    init = db.get_initiative(con, iid)
    cfg = form_to_brief(f, config_of(init), (init["description"] or "").strip())
    # Проверку состава утверждений делает ДВИЖОК, а не форма: правило «applied обязан
    # указать maps_to, ignored обязан назвать причину» живёт в одном месте, иначе веб
    # и навык начнут принимать разные брифы. Приложение только показывает текст ошибки.
    try:
        calc.validate_config(cfg)
    except calc.ConfigError as e:
        only_brief = [ln for ln in str(e).splitlines() if "brief" in ln]
        db.update_initiative(con, iid, config=cfg)      # ввод не теряем
        return RedirectResponse(
            f"/initiative/{iid}/brief?error=" + quote("\n".join(only_brief) or str(e)),
            status_code=303)
    db.update_initiative(con, iid, config=cfg)
    if f.get("action") == "save":
        return RedirectResponse(f"/initiative/{iid}/brief", status_code=303)
    return RedirectResponse(f"/initiative/{iid}/pilot", status_code=303)


# ------------------------------------------------------------------ шаг 3: пилот
# Параметры, которые прошедший пилот имеет право переопределить. Список берётся
# из движка, а не переписывается здесь: разойдясь, форма предложила бы задать то,
# что движок отвергнет строгой валидацией.
PILOT_METRIC_CHOICES = sorted(calc.PILOT_TARGET)
PILOT_FLAGS = ("control_group", "randomized", "matched_at_baseline")
PILOT_TEXT = ("name", "geography", "rollout_geography", "audience")


def form_to_pilot(form, cfg):
    """Поля формы → раздел `pilot`. Пустая форма убирает раздел целиком."""
    if not form.get("has_pilot"):
        cfg.pop("pilot", None)
        return cfg
    p = {}
    for k in PILOT_TEXT:
        v = (form.get(f"pilot.{k}") or "").strip()
        if v:
            p[k] = v
    # «не указано» — это отсутствие ответа, а не ответ: движок обязан спросить,
    # а не подставить умолчание за человека
    _ma = (form.get("pilot.metrics_are") or "").strip()
    if _ma:
        p["metrics_are"] = _ma
    for k in PILOT_FLAGS:
        p[k] = bool(form.get(f"pilot.{k}"))
    for k in ("sample_size", "control_size", "duration_months",
              "baseline_rate", "observed_rate", "scale_haircut", "metric_cv",
              "tail_months"):
        raw = form.get(f"pilot.{k}")
        if raw not in (None, ""):
            p[k] = _num(raw)
    # метрики: строки «параметр → значение», пустые пропускаем
    metrics, tail, i = {}, {}, 0
    while any(k.startswith(f"metric.{i}.") for k in form.keys()):
        key = (form.get(f"metric.{i}.key") or "").strip()
        raw = form.get(f"metric.{i}.value")
        if key and raw not in (None, ""):
            metrics[key] = _num(raw)
            # хвост без средней бессмыслен: сравнивать не с чем
            tail_raw = form.get(f"metric.{i}.tail")
            if tail_raw not in (None, ""):
                tail[key] = _num(tail_raw)
        i += 1
    if metrics:
        p["metrics"] = metrics
    if tail:
        p["metrics_tail"] = tail
    cfg["pilot"] = p
    return cfg


def pilot_checks(cfg):
    """Три проверки пилота — целиком из движка.

    География, контрольная группа и мощность выборки считаются в `apply_pilot()`.
    Повторять их здесь значило бы завести третью копию правил: она разойдётся
    с движком при первой правке, как это уже случилось с флагами.
    """
    if not cfg.get("pilot"):
        return None
    probe = calc.apply_pilot(copy.deepcopy(cfg))
    return {"info": probe.get("_pilot"),
            "notes": [n for n in probe.get("_notes", []) if "ПИЛОТ" in n or "ОТБОР" in n]}


@app.get("/initiative/{iid}/pilot", response_class=HTMLResponse)
def pilot_form(request: Request, iid: int, error: str = "", foreign: str = ""):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    return page(request, "pilot.html", init=init, step="pilot", error=error,
                foreign=json.loads(foreign) if foreign else {},
                foreign_steps=ERROR_STEP,
                pilot=cfg.get("pilot") or {}, checks=pilot_checks(cfg),
                metric_choices=PILOT_METRIC_CHOICES,
                metric_labels={p.key: p.label for p in params.ALL})


@app.post("/initiative/{iid}/pilot")
async def pilot_save(request: Request, iid: int):
    f = await request.form()
    init = db.get_initiative(con, iid)
    cfg = form_to_pilot(f, config_of(init))
    try:
        calc.validate_config(cfg)
    except calc.ConfigError as e:
        # Пилот всё равно записываем: ввод человека не теряем, а ошибка может быть
        # вообще не про него — см. split_error().
        db.update_initiative(con, iid, config=cfg)
        mine, foreign = split_error(e, "pilot")
        return RedirectResponse(
            f"/initiative/{iid}/pilot?error=" + quote(mine)
            + ("&foreign=" + quote(json.dumps(foreign, ensure_ascii=False)) if foreign else ""),
            status_code=303)
    db.update_initiative(con, iid, config=cfg)
    if f.get("action") == "save":
        return RedirectResponse(f"/initiative/{iid}/pilot", status_code=303)
    return RedirectResponse(f"/initiative/{iid}/segments", status_code=303)


@app.post("/api/pilot/preview")
async def api_pilot_preview(request: Request):
    """Мощность выборки и грейд переноса — до сохранения, пока человек ещё правит.

    «Пилот на 800 человек не докажет 3 п.п.» должно появляться в момент ввода,
    а не после того, как на этих цифрах уже построили вердикт.
    """
    body = await request.json()
    probe = params.defaults()
    probe["pilot"] = {k: v for k, v in (body.get("pilot") or {}).items() if v not in (None, "")}
    try:
        calc.validate_config(probe)
    except calc.ConfigError as e:
        return JSONResponse({"errors": [ln.strip(" -") for ln in str(e).splitlines()[1:]]})
    out = calc.apply_pilot(probe)
    return JSONResponse({"info": out.get("_pilot"), "errors": [],
                         "notes": [n for n in out.get("_notes", [])
                                   if "ПИЛОТ" in n or "ОТБОР" in n]})


@app.get("/initiative/{iid}/segments", response_class=HTMLResponse)
def segments_form(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = config_of(init)
    chosen = {s["id"]: s for s in (cfg.get("audience", {}).get("segments") or [])}
    known = {sid: params.segment_defaults(sid, init["product"]) for sid in calc.SEGMENTS}
    return page(request, "segments.html", init=init, cfg=cfg, chosen=chosen, step="segments",
                known=known, has_value=bool(params.product_values(init["product"])))


@app.post("/initiative/{iid}/segments")
async def segments_save(request: Request, iid: int):
    init = db.get_initiative(con, iid)
    cfg = form_to_config(await request.form(), config_of(init))
    cfg = apply_segment_defaults(cfg, init["product"])
    db.update_initiative(con, iid, config=cfg, status="segments")
    return RedirectResponse(f"/initiative/{iid}/panel", status_code=303)


@app.post("/api/segments/preview")
async def segments_preview(request: Request):
    """Возрастные ограничения — из движка, а не из копии правил в JavaScript."""
    body = await request.json()
    product = body.get("product") or ""
    rows = []
    for s in body.get("segments", []):
        sid = s.get("id")
        if sid not in calc.SEGMENTS:
            continue
        d = params.segment_defaults(sid, product) or {}
        size = _num(s.get("target_size"), 0) or d.get("target_size") or 0
        row = {"id": sid, "target_size": size}
        if d.get("reachable_share") is not None:
            row["reachable_share"] = d["reachable_share"]
        rows.append(row)
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
                step="assumptions", error=error, conf=calc.confidence_index(cfg),
                prefilled=prefilled_summary(init, cfg),
                ready=params.channels_ready(cfg))


@app.post("/initiative/{iid}/assumptions")
async def assumptions_save(request: Request, iid: int):
    f = await request.form()
    init = db.get_initiative(con, iid)
    cfg = form_to_config(f, config_of(init))
    # Вложенные блоки разбираются общей функцией: добавить следующий — одна строка здесь
    # и метаданные в реестре, без новой верстки и нового парсера.
    for section in ("linked_client", "companion", "partner", "natural_effect"):
        cfg = form_to_nested(f, cfg, section)
    # Части базы разбираются ПОСЛЕ блоков: до этого раздела ещё может не быть,
    # а класть части в несуществующий раздел значит воскресить снятый блок.
    for section in params.PARTS_SECTIONS:
        cfg = form_to_parts(f, cfg, section)
    for section in params.SPLIT_SECTIONS:
        cfg = form_to_split(f, cfg, section)
    cfg = form_to_seasonality(f, cfg)
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


@app.post("/calculation/{cid}/delete")
def calculation_delete(request: Request, cid: int, back: str = Form("/")):
    db.delete_calculation(con, cid)
    return RedirectResponse(back or "/", status_code=303)


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
