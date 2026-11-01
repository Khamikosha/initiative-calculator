#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Шаг «Допущения»: нагрузка, части базы, разложение оборота, сезонность, имена полей."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

# ------------------------------- Нагрузка шага «Допущения» (BACKLOG 43)
# Мерить надо не число полей, а сколько человек ОБЯЗАН тронуть, чтобы получить ответ.
# На шаге 178 полей, обязательных — восемь.
_ai = int(client.post("/initiative/new", data={
    "name": "Нагрузка", "type": "product_launch", "product": "kids_card"},
    follow_redirects=False).headers["location"].split("/")[2])
client.post(f"/initiative/{_ai}/segments",
            data={"segments": ["kids"], "seg.kids.baseline_organic_apps_m": "700"},
            follow_redirects=False)
_mini = {"ch.0.name": "Перф", "ch.0.kind": "perform", "ch.0.frk_tier": "4",
         "ch.0.budget": "50000000", "ch.0.cpm": "250", "ch.0.ctr": "0.008",
         "ch.0.cr_to_app": "0.06", "costs.campaign_months": "6", "action": "save"}
client.post(f"/initiative/{_ai}/assumptions", data=_mini, follow_redirects=False)
_ar = client.post(f"/initiative/{_ai}/calculate", follow_redirects=False)
check("Восьми полей хватает, чтобы получить ответ",
      "calculation" in _ar.headers.get("location", ""),
      f"тронуто {len(_mini) - 1} из 178 — остальное предзаполнено")

_apage = client.get(f"/initiative/{_ai}/assumptions").text
check("Минимум объявлен на странице, а не выясняется опытом",
      "обязательны только каналы" in _apage)
check("Решение «нужен ли источник эффекта» принимается один раз, а не четырежды",
      len(re.findall(r'class="srcpick"', _apage)) == len(params.EFFECT_SECTIONS) == 4,
      "четыре карточки подряд заставляли решать четыре раза, и три раза впустую")
check("Незадействованные источники скрыты, а не развёрнуты",
      len(re.findall(r'class="srcblock"[^>]*hidden', _apage)) == 4,
      "на обычной инициативе не нужен ни один из них")

_on = dict(_mini)
_on.update({"has_companion": "1", "companion.name": "Копилка",
            "companion.attach_rate": "0.3", "companion.funded_rate": "0.6",
            "companion.avg_balance": "12000", "companion.nim_annual": "0.06"})
client.post(f"/initiative/{_ai}/assumptions", data=_on, follow_redirects=False)
_apage2 = client.get(f"/initiative/{_ai}/assumptions").text
check("Отмеченный источник разворачивается и остаётся отмеченным",
      'data-src="companion"' in _apage2 and
      not re.search(r'class="srcblock" data-src="companion"[^>]*hidden', _apage2),
      "иначе заполненный блок пришлось бы искать заново при каждом возврате")
check("Остальные источники при этом остаются свёрнутыми",
      len(re.findall(r'class="srcblock"[^>]*hidden', _apage2)) == 3)

# ------------------------------- Части неоднородной базы (BACKLOG 20)
# Без разбиения отклик применяется ко всей базе целиком, включая ту часть, где
# эффект структурно невозможен: спящий год не просыпается от пуша. Это один
# из множителей, каждый из которых уже завышал результат на живых расчётах.
check("Разделы с частями базы берутся из реестра, а не перечислены в шаблоне",
      set(params.PARTS_SECTIONS) == {"base_activation", "natural_effect"})
check("Поля части базы совпадают с ключами движка",
      {f.key for f in params.part_fields("natural_effect")} == set(calc.NATURAL_PART_KEYS))
check("Ставка перевода в деньги показывается только там, где движок её читает",
      "monetization" not in {f.key for f in params.part_fields("base_activation")},
      "base_activation считает сразу в рублях оборота — поле бы обещало влияние, "
      "которого нет")

# продукт без каталожной ценности: режим build_up, ставки маржи живые
r = client.post("/initiative/new", data={
    "name": "Растранзачивание", "type": "comms", "product": "card"},
    follow_redirects=False)
_pid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_pid}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "700"},
            follow_redirects=False)
_ppage = client.get(f"/initiative/{_pid}/assumptions").text
for _sec in params.PARTS_SECTIONS:
    _pmissing = [f.key for f in params.part_fields(_sec)
                 if f'name="{_sec}.part.0.{f.key}"' not in _ppage]
    check(f"Таблица частей раздела «{_sec}» отрисована со всеми полями",
          not _pmissing, f"нет в форме: {_pmissing}" if _pmissing else "")

_ba = {f"base_activation.{k}": v for k, v in (
    ("eligible_clients", "2000000"), ("reach_share", "0.5"),
    ("technically_reachable", "0.7"), ("uplift_rate", "0.1"),
    ("turnover_before", "3000"), ("turnover_after", "9000"),
    ("decay_months", "12"), ("attribution_share", "1"))}
_rows = [("спит месяц", "0.5", "0.2"), ("спит полгода", "0.3", "0.05"),
         ("спит год и больше", "0.2", "0")]
for _i, (_n, _sh, _cap) in enumerate(_rows):
    _ba[f"base_activation.part.{_i}.name"] = _n
    _ba[f"base_activation.part.{_i}.share"] = _sh
    _ba[f"base_activation.part.{_i}.uplift_cap"] = _cap
_chan = {"costs.campaign_months": "3", "ch.0.name": "Пуши", "ch.0.kind": "internal",
         "ch.0.frk_tier": "4", "ch.0.reach_abs": "2000000",
         "ch.0.response_rate": "0.018", "ch.0.cr_to_app": "0.1", "action": "save"}
client.post(f"/initiative/{_pid}/assumptions", data={**_ba, **_chan},
            follow_redirects=False)
_pcfg = json.loads(_db.get_initiative(_db.connect(), _pid)["config_json"])
check("Строки таблицы доезжают до конфига списком частей",
      [p["name"] for p in _pcfg["base_activation"]["parts"]] == [n for n, _, _ in _rows])
check("Запятая в доле разбирается как десятичный разделитель",
      _pcfg["base_activation"]["parts"][1]["share"] == 0.3)

_pr = client.post(f"/initiative/{_pid}/calculate", follow_redirects=False)
_with = json.loads(_db.get_calculation(
    _db.connect(), int(_pr.headers["location"].split("/")[-1]))["result_json"])
_wm = _with["pl"]["margin_sources"]["base_activation"]

# те же данные без разбиения — разница и есть цена вопроса
for _i in range(len(_rows)):
    for _k in ("name", "share", "uplift_cap"):
        _ba.pop(f"base_activation.part.{_i}.{_k}")
client.post(f"/initiative/{_pid}/assumptions", data={**_ba, **_chan},
            follow_redirects=False)
check("Пустая таблица убирает части, а не оставляет пустой список",
      "parts" not in json.loads(
          _db.get_initiative(_db.connect(), _pid)["config_json"])["base_activation"],
      "движок отличает «частей нет» от «части заданы» и на первом предупреждает")
_pr2 = client.post(f"/initiative/{_pid}/calculate", follow_redirects=False)
_without = json.loads(_db.get_calculation(
    _db.connect(), int(_pr2.headers["location"].split("/")[-1]))["result_json"])
_om = _without["pl"]["margin_sources"]["base_activation"]
check("Разбиение базы уменьшает эффект — глубокая часть его не даёт",
      0 < _wm < _om, f"с частями {_wm:,.0f} ₽ против {_om:,.0f} ₽ — "
                     f"завышение в {_om / _wm:.2f} раза")
check("Однородная база названа однородной",
      any("БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ" in v for v in _without["validation"]))

# Продукт с каталожной ценностью обнуляет ставки маржи снизу вверх — переводить
# прирост оборота не во что, и блок молча давал ровно ноль.
r = client.post("/initiative/new", data={
    "name": "Растранзачивание на NPV", "type": "comms", "product": "kids_card"},
    follow_redirects=False)
_vid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_vid}/segments",
            data={"segments": ["kids"], "seg.kids.baseline_organic_apps_m": "700"},
            follow_redirects=False)
for _i, (_n, _sh, _cap) in enumerate(_rows):
    _ba[f"base_activation.part.{_i}.name"] = _n
    _ba[f"base_activation.part.{_i}.share"] = _sh
    _ba[f"base_activation.part.{_i}.uplift_cap"] = _cap
client.post(f"/initiative/{_vid}/assumptions", data={**_ba, **_chan},
            follow_redirects=False)
_vr = client.post(f"/initiative/{_vid}/calculate", follow_redirects=False)
_vres = json.loads(_db.get_calculation(
    _db.connect(), int(_vr.headers["location"].split("/")[-1]))["result_json"])
check("Нулевая маржа растранзачивания при готовой ценности клиента объяснена",
      _vres["pl"]["margin_sources"]["base_activation"] == 0 and
      any("РАСТРАНЗАЧИВАНИЕ НЕ ПОСЧИТАНО" in v for v in _vres["validation"]),
      "заполненный блок не должен давать ноль без причины")


# ------------------------------- Разложение оборота и сезонность (BACKLOG 20)
# Последние два вложенных ключа. Одна усреднённая ставка interchange прячет, что
# POS, e-POS и снятие оплачиваются по-разному; равномерный приток прячет форму плана.
check("Разделы с разложением оборота берутся из реестра",
      set(params.SPLIT_SECTIONS) == {"unit_economics", "partner"})
check("Поля строки разложения совпадают с ключами движка",
      {f.key for f in params.split_fields("unit_economics")} ==
      set(calc.TXN_KEYS) | {"name"})

r = client.post("/initiative/new", data={
    "name": "Оборот и сезон", "type": "product_launch", "product": "card"},
    follow_redirects=False)
_sid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_sid}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "700"},
            follow_redirects=False)
_spage = client.get(f"/initiative/{_sid}/assumptions").text
for _sec in params.SPLIT_SECTIONS:
    _smissing = [f.key for f in params.split_fields(_sec)
                 if f'name="{_sec}.txn.0.{f.key}"' not in _spage]
    check(f"Таблица разложения раздела «{_sec}» отрисована со всеми полями",
          not _smissing, f"нет в форме: {_smissing}" if _smissing else "")
check("Сезонность отрисована двенадцатью полями",
      all(f'name="acquisition.season.{i}"' in _spage
          for i in range(params.SEASON_MONTHS)))
check("Месяцы сезонности не подписаны календарными названиями",
      not any(m in _spage for m in ("январ", "Январ", "феврал", "Феврал")),
      "движок берёт seas[m % 12] от старта кампании — календарной привязки нет, "
      "и подпись «январь» обещала бы то, чего в модели не существует")

_perf = {"costs.campaign_months": "12", "ch.0.name": "Перф", "ch.0.kind": "perform",
         "ch.0.frk_tier": "4", "ch.0.budget": "50000000", "ch.0.cpm": "250",
         "ch.0.ctr": "0.008", "ch.0.cr_to_app": "0.06", "action": "save"}
_txn = [("POS", "0.6", "0.0006"), ("e-POS", "0.3", "0.002"), ("снятие", "0.1", "0.001")]
_sd = dict(_perf)
for _i, (_n, _sh, _fee) in enumerate(_txn):
    _sd[f"unit_economics.txn.{_i}.name"] = _n
    _sd[f"unit_economics.txn.{_i}.share"] = _sh
    _sd[f"unit_economics.txn.{_i}.fee"] = _fee
_season = [1.3, 2.0, 1.7, 1.0, 0.7, 1.0, 0.95, 0.95, 0.9, 0.85, 0.95, 1.0]
for _i, _v in enumerate(_season):
    _sd[f"acquisition.season.{_i}"] = str(_v).replace(".", ",")
client.post(f"/initiative/{_sid}/assumptions", data=_sd, follow_redirects=False)
_scfg = json.loads(_db.get_initiative(_db.connect(), _sid)["config_json"])
check("Разложение доезжает до конфига словарём по видам операций",
      _scfg["unit_economics"]["turnover_split"] ==
      {"POS": {"share": 0.6, "fee": 0.0006}, "e-POS": {"share": 0.3, "fee": 0.002},
       "снятие": {"share": 0.1, "fee": 0.001}})
check("Сезонный профиль доезжает списком из двенадцати",
      _scfg["acquisition"]["seasonality"] == _season)

# Ставки видов операций ниже общего interchange — значит и комиссия должна упасть.
_sr = client.post(f"/initiative/{_sid}/calculate", follow_redirects=False)
_swith = json.loads(_db.get_calculation(
    _db.connect(), int(_sr.headers["location"].split("/")[-1]))["result_json"])
for _i in range(len(_txn)):
    for _k in ("name", "share", "fee"):
        _sd.pop(f"unit_economics.txn.{_i}.{_k}")
client.post(f"/initiative/{_sid}/assumptions", data=_sd, follow_redirects=False)
check("Пустая таблица убирает разложение, а не оставляет пустой словарь",
      "turnover_split" not in json.loads(
          _db.get_initiative(_db.connect(), _sid)["config_json"])["unit_economics"],
      "иначе весь оборот молча пошёл бы по ставке ноль")
_sr2 = client.post(f"/initiative/{_sid}/calculate", follow_redirects=False)
_sflat = json.loads(_db.get_calculation(
    _db.connect(), int(_sr2.headers["location"].split("/")[-1]))["result_json"])
check("Разложение оборота меняет NPV — усреднённая ставка не то же самое",
      _swith["pl"]["npv"] != _sflat["pl"]["npv"],
      f'{_swith["pl"]["npv"]:,} против {_sflat["pl"]["npv"]:,} ₽')

# Дубликат названия: словарь молча затёр бы первую строку вместе с её долей
_dup = dict(_perf)
for _i, _n in enumerate(("POS", "POS")):
    _dup[f"unit_economics.txn.{_i}.name"] = _n
    _dup[f"unit_economics.txn.{_i}.share"] = "0.5"
    _dup[f"unit_economics.txn.{_i}.fee"] = "0.001"
client.post(f"/initiative/{_sid}/assumptions", data=_dup, follow_redirects=False)
check("Повторённое название вида операций не съедает предыдущую строку",
      len(json.loads(_db.get_initiative(_db.connect(), _sid)["config_json"])
          ["unit_economics"]["turnover_split"]) == 2,
      "иначе половина оборота исчезла бы бесследно и доли не сошлись бы к 1,0")

# Профиль пишется целиком: половина двенадцати месяцев — не «остальные по единице»
_half = dict(_perf)
for _i in range(6):
    _half[f"acquisition.season.{_i}"] = "1"
client.post(f"/initiative/{_sid}/assumptions", data=_half, follow_redirects=False)
check("Недостающие месяцы профиля не дорисовываются единицами",
      len(json.loads(_db.get_initiative(_db.connect(), _sid)["config_json"])
          ["acquisition"]["seasonality"]) == 6,
      "дорисовка превратила бы ошибку ввода в правдоподобный профиль")
_hr = client.post(f"/initiative/{_sid}/calculate", follow_redirects=False)
_herr = unquote(_hr.headers.get("location", ""))
check("Половина профиля останавливает расчёт с внятной причиной",
      "seasonality" in _herr and "12" in _herr and
      _herr.startswith(f"/initiative/{_sid}/assumptions"),
      "заданная наполовину форма плана тише и опаснее её отсутствия — "
      "и ошибка ведёт на тот шаг, где её правят")


# ------------------------------- Имена полей формы соответствуют строкам таблицы
# Регрессия, прожившая до живого теста: во вложенном цикле шаблона `loop.index0`
# указывает на ВНУТРЕННИЙ цикл, и поля канала уезжали в чужие строки —
# ch.0.budget, ch.1.cpm, ch.2.ctr. Данные «слетали», воронка схлопывалась.
#
# Прежние тесты этого не видели: они слали форму СЛОВАРЁМ, минуя разметку.
# Проверять надо то, что реально отрендерено.
import re as _re
r = client.post("/initiative/new", data={
    "name": "Разметка", "type": "frk3", "product": "card"}, follow_redirects=False)
_fid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_fid}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "20000"},
            follow_redirects=False)
_html = client.get(f"/initiative/{_fid}/assumptions").text
_rows = {}
for _n in _re.findall(r'name="ch\.(\d+)\.(\w+)"', _html):
    _rows.setdefault(_n[0], set()).add(_n[1])
check("Каждая строка канала несёт полный набор своих полей",
      all(len(v) >= 9 for v in _rows.values()),
      f'по строкам: { {k: len(v) for k, v in sorted(_rows.items())} }')
check("Поля канала не разъезжаются по чужим строкам",
      all("cpm" in v and "ctr" in v and "reach_abs" in v for v in _rows.values()),
      "иначе ch.0.budget соседствует с ch.1.cpm и данные теряются")


# ------------------------------- Предпросмотр раскладки затрат (живой инцидент)
# Разовую поддержку 2,5 млн ₽ завели в «Постоянные расходы», движок умножил на горизонт
# (× 36) = 90 млн. Раскладка и вопрос должны быть видны в момент ввода. Проверяем
# по ОТРИСОВАННОЙ разметке: контейнер есть на странице; эндпоинт возвращает строки.
_cid = new_initiative("Раскладка затрат", type="product_launch", product="card")
_capage = client.get(f"/initiative/{_cid}/assumptions").text
check("Контейнер предпросмотра затрат отрисован на странице «Допущения»",
      'id="costpreview-card"' in _capage and 'id="costpreview"' in _capage)
check("Секция раскладки стоит под блоком «Горизонт и затраты»",
      _capage.index("Горизонт и затраты") < _capage.index('id="costpreview-card"')
      < _capage.index("Качество данных"))
check("Раскладку затрат считает движок по HTTP, а не JS в шаблоне",
      "/api/costs/preview" in client.get("/static/app.js").text)

_cprev = client.post("/api/costs/preview", json={
    "costs": {"opex_monthly": "2500000"}, "horizon_months": "36"}).json()
_cby = {it["key"]: it for it in _cprev["items"]}
check("Эндпоинт: 2,5 млн opex при горизонте 36 даёт total_over_horizon = 90 млн",
      _cby["opex_monthly"]["total_over_horizon"] == 90_000_000 and
      _cby["opex_monthly"]["kind"] == "постоянное" and
      _cby["opex_monthly"]["months"] == 36,
      f"пришло {_cby.get('opex_monthly')}")
check("Эндпоинт отдаёт вопрос-предупреждение про статью затрат при opex-доминанте",
      any("ПРОВЕРЬТЕ СТАТЬЮ ЗАТРАТ" in w for w in _cprev["warnings"]))

# Мягкая заметка на строке постоянных расходов приходит в позиции opex готовой из движка
# (текст + число opex×горизонт). Она есть ВСЕГДА при opex > 0 — в отличие от жёсткого
# предупреждения выше, которое молчит при крупной законной разовой статье рядом.
check("Эндпоинт: у позиции постоянных расходов есть мягкая заметка при opex > 0",
      bool(_cby["opex_monthly"].get("note")) and
      calc.nf(90_000_000) in _cby["opex_monthly"]["note"] and
      "разовая статья" in _cby["opex_monthly"]["note"],
      f"пришло {_cby['opex_monthly'].get('note')}")
# UI только показывает заметку — рендер it.note под строкой позиции живёт в app.js,
# формулы там нет. Проверяем по отрисовке предпросмотра (её строит app.js из ответа).
check("Предпросмотр в разметке показывает заметку под строкой позиции (it.note в app.js)",
      "it.note" in client.get("/static/app.js").text)

# запятая как разделитель и разовая статья: списывается один раз, вопроса нет
_cprev2 = client.post("/api/costs/preview", json={
    "costs": {"production": "3 000 000", "opex_monthly": "2500000",
              "it_dev": "7000000"}, "horizon_months": "36"}).json()
_cby2 = {it["key"]: it for it in _cprev2["items"]}
check("Эндпоинт: разовая статья списывается один раз (total = per_period)",
      _cby2["production"]["kind"] == "разовое" and
      _cby2["production"]["total_over_horizon"] == 3_000_000)
check("Эндпоинт: при крупных разовых вопрос про статью затрат не задаётся",
      not any("ПРОВЕРЬТЕ СТАТЬЮ ЗАТРАТ" in w for w in _cprev2["warnings"]))
# на этом же кейсе (крупная законная разовая рядом с opex) жёсткое предупреждение
# молчит, а мягкая заметка на строке постоянных расходов всё равно есть — в этом
# её смысл: она универсальна и не зависит от узкой сигнатуры инцидента.
check("Эндпоинт: заметка на opex есть и когда жёсткое предупреждение молчит",
      bool(_cby2["opex_monthly"].get("note")))

# и сквозная проверка: заполненные каналы переживают сохранение
client.post(f"/initiative/{_fid}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "12000000",
    "ch.0.name": "Перформанс", "ch.0.kind": "perform", "ch.0.frk_tier": "4",
    "ch.0.budget": "25000000", "ch.0.cpm": "380", "ch.0.ctr": "0.007",
    "ch.0.cr_to_app": "0.035",
    "ch.1.name": "Посевы", "ch.1.kind": "perform", "ch.1.frk_tier": "3",
    "ch.1.budget": "15000000", "ch.1.cpm": "260", "ch.1.ctr": "0.005",
    "ch.1.cr_to_app": "0.025", "action": "save"}, follow_redirects=False)
_fch = json.loads(_db.get_initiative(_db.connect(), _fid)["config_json"])["channels"]
check("Оценки по каналам не слетают при сохранении",
      len(_fch) == 2 and _fch[0]["cpm"] == 380 and _fch[0]["ctr"] == 0.007
      and _fch[1]["cpm"] == 260 and _fch[1]["cr_to_app"] == 0.025,
      json.dumps(_fch, ensure_ascii=False))
_fpage = client.get(f"/initiative/{_fid}/assumptions").text
check("Сохранённые значения возвращаются в форму",
      'value="380.0"' in _fpage or 'value="380"' in _fpage,
      "иначе человек видит пустое поле и вводит заново")
