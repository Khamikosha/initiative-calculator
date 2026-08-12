#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Договор реестра с формой и движком: сквозной путь параметра, ставки, проникновение, каталог."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

# ------------------------------- Сквозная проверка реестра: форма → конфиг → движок
# У реестра параметров ЧЕТЫРЕ потребителя: схема движка, разбор формы, шаблоны
# и уже сохранённые конфиги. `_assert_consistent()` знает только про первого.
# Из-за этого добавление одного поля сломало три пути из четырёх за один день:
# поле утекло на общий уровень, конфиг перестал проходить проверку, а сохранённая
# инициатива стала неоткрываемой.
#
# Этот тест проходит ВЕСЬ путь для КАЖДОГО параметра разом: поле есть в форме,
# введённое значение доезжает до конфига, конфиг принимается движком. Точечные
# проверки по отдельным формам такого не ловят — они про те формы, которые правили.
_ri = int(client.post("/initiative/new", data={
    "name": "Сквозная", "type": "product_launch", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
client.post(f"/initiative/{_ri}/segments",
            data={"segments": ["youth"]}, follow_redirects=False)
_rpage = client.get(f"/initiative/{_ri}/assumptions").text
_spage_all = client.get(f"/initiative/{_ri}/segments").text

_FROM_INITIATIVE = {"name", "type", "product"}


def _probe_value(p):
    """Значение внутри допустимого диапазона — иначе провалится валидация, а не путь."""
    if p.fmt == "text":
        return None
    lo, hi = p.rng or (0, 1)
    if p.fmt in ("pct", "pct2"):
        return 0.11
    if p.fmt == "int":
        return int(max(lo, min(hi, 7)))
    return max(lo, min(hi, 3.0)) if hi <= 10 else max(lo, min(hi, 1234.0))


_missing_field, _lost_value, _out = [], [], {}
for _sec, _, _ps in params.SECTIONS:
    for _p in _ps:
        if _sec == "meta" and _p.key in _FROM_INITIATIVE:
            continue
        if (_sec, _p.key) in params.SEGMENT_ONLY:
            # общего значения не имеет: место такому полю на шаге «Сегменты»
            if f'name="seg.youth.{_p.key}"' not in _spage_all:
                _missing_field.append(f"seg.{_p.key}")
            if f'name="{_sec}.{_p.key}"' in _rpage:
                _lost_value.append(f"{_sec}.{_p.key} — посегментный ключ на общем уровне")
            continue
        # Параметр обязан быть заполнимым ХОТЬ ГДЕ-ТО: часть посегментных полей
        # намеренно живёт на шаге «Сегменты», а из общей формы скрыта, чтобы одно
        # и то же число не задавалось в двух местах.
        _on_assumptions = f'name="{_sec}.{_p.key}"' in _rpage
        _on_segments = f'name="seg.youth.{_p.key}"' in _spage_all
        if not (_on_assumptions or _on_segments):
            _missing_field.append(f"{_sec}.{_p.key}")
            continue
        if not _on_assumptions:
            continue        # задаётся посегментно, общего значения в этой форме нет
        _v = _probe_value(_p)
        if _v is not None:
            _out[f"{_sec}.{_p.key}"] = str(_v)

check("Каждый параметр реестра есть в форме — иначе задать его нечем",
      not _missing_field, "нет полей: " + ", ".join(_missing_field[:6]))

_out.update({"costs.campaign_months": "6", "ch.0.name": "К", "ch.0.kind": "internal",
             "ch.0.frk_tier": "4", "ch.0.reach_abs": "1000000",
             "ch.0.response_rate": "0.02", "ch.0.cr_to_app": "0.1", "action": "save"})
_rr = client.post(f"/initiative/{_ri}/assumptions", data=_out, follow_redirects=False)
check("Конфиг из всех полей реестра принимается движком",
      "error=" not in _rr.headers.get("location", ""),
      unquote(_rr.headers.get("location", ""))[:150])

_rcfg = json.loads(_db.get_initiative(_db.connect(), _ri)["config_json"])
for _field, _val in _out.items():
    if _field.startswith(("ch.", "costs.campaign")) or _field == "action":
        continue
    _sec, _key = _field.split(".", 1)
    _got = (_rcfg.get(_sec) or {}).get(_key)
    if _got is None or abs(float(_got) - float(_val)) > 1e-6:
        _lost_value.append(f"{_field}: отправили {_val}, в конфиге {_got}")
check("Введённое значение доезжает до конфига по каждому параметру",
      not _lost_value, "; ".join(_lost_value[:4]))
check("И расчёт по такому конфигу проходит",
      "calculation" in client.post(f"/initiative/{_ri}/calculate",
                                   follow_redirects=False).headers.get("location", ""))

# Разбор формы обязан отбрасывать посегментный ключ, ЧТО БЫ НИ ПРИШЛО. Проверка
# «поля нет в шаблоне» сюда не годится: шаблон один раз уже отрендерил его случайно,
# и защитой оказался только разбор. Поэтому шлём ключ намеренно.
_forced = dict(_out)
for _sec, _key in params.SEGMENT_ONLY:
    _forced[f"{_sec}.{_key}"] = "250000"
_fr = client.post(f"/initiative/{_ri}/assumptions", data=_forced, follow_redirects=False)
_fcfg = json.loads(_db.get_initiative(_db.connect(), _ri)["config_json"])
check("Посегментный ключ не попадает в раздел, даже если форма его прислала",
      all(_key not in (_fcfg.get(_sec) or {}) for _sec, _key in params.SEGMENT_ONLY) and
      "error=" not in _fr.headers.get("location", ""),
      "именно так конфиг стал неоткрываемым: движок отвергал его на каждом шаге")


def _safe_validate(cfg):
    try:
        calc.validate_config(cfg); return True
    except calc.ConfigError:
        return False


# Старый конфиг с посегментным ключом на уровне раздела чинится при чтении.
# Такая запись могла остаться от прежней версии, прийти из бэкапа или правки руками.
# Человек упирался в «неизвестный ключ» на КАЖДОМ шаге и не мог его убрать: поля
# такого в форме нет, а конфиг правится только через форму.
_hi = int(client.post("/initiative/new", data={
    "name": "Старый конфиг", "type": "product_launch", "product": "broker"},
    follow_redirects=False).headers["location"].split("/")[2])
_hcon = _db.connect()
_hcfg = json.loads(_db.get_initiative(_hcon, _hi)["config_json"])
_hcfg.setdefault("audience", {})["product_clients"] = 250000     # порча, как в проде
_db.update_initiative(_hcon, _hi, config=_hcfg)
check("Испорченный ключ действительно ломает движок",
      not _safe_validate(json.loads(_db.get_initiative(_db.connect(), _hi)["config_json"])),
      "иначе тест ничего не проверяет")
_hload = app_mod.config_of(_db.get_initiative(_db.connect(), _hi))
check("Загрузка конфига убирает посегментный ключ с уровня раздела",
      "product_clients" not in (_hload.get("audience") or {}))
check("И после этого страница открывается без ошибки движка",
      "Движок отверг конфиг" not in client.get(f"/initiative/{_hi}/assumptions").text)

# Ставка, обнулённая взвешиванием (BACKLOG 58). Найдено дважды на живых постановках:
# на NIM (брокерский счёт остатка не держит) и на интерчейндже (оборот основного
# продукта ноль, а прирост оборота у растранзачивания есть — цена 7,7 раза).
def _rate_case(turnover, interchange=0.015, balance=15000, nim=0.01):
    cfg = json.loads(json.dumps(params.defaults()))
    cfg["meta"].update(name="Ставки", type="product_launch", product="broker",
                       horizon_months=36)
    cfg["audience"] = {"segments": [{"id": "teens", "target_size": 6_000_000,
                       "reachable_share": 1.0, "baseline_organic_apps_m": 0}]}
    cfg["unit_economics"].update(mode="build_up", avg_balance=balance, nim_annual=nim,
        monthly_turnover=turnover, interchange_rate=interchange, cashback_rate=0,
        fee_income_m=15, servicing_cost_m=5, cor_annual=0, monthly_churn=0.03,
        ramp_months=3)
    cfg["base_activation"] = {"name": "Рост активности", "eligible_clients": 100_000,
        "technically_reachable": 1.0, "reach_share": 1.0, "uplift_rate": 0.05,
        "attribution_share": 1.0, "turnover_before": 0, "turnover_after": 3000,
        "balance_before": 0, "balance_after": 8000, "decay_months": 24}
    cfg["channels"] = [{"name": "П", "kind": "internal", "frk_tier": 4,
        "reach_abs": 3_000_000, "response_rate": 0.08, "cr_to_app": 0.25}]
    cfg["costs"].update(campaign_months=6, production=1_500_000)
    return calc.analyze(calc.prepare(cfg))


_z = _rate_case(turnover=0)
_nz = _rate_case(turnover=8000)
check("Ставка, обнулённая взвешиванием, названа вслух",
      any("ОБНУЛЕНА ВЗВЕШИВАНИЕМ" in v and "interchange_rate" in v
          for v in _z["validation"]),
      "заданная ставка молча превращалась в ноль, и часть эффекта исчезала")
check("Сказано, какое поле заполнить, чтобы ставка выжила",
      any("monthly_turnover" in v for v in _z["validation"] if "ОБНУЛЕНА" in v))
check("Когда база ненулевая, предупреждения нет",
      not any("ОБНУЛЕНА ВЗВЕШИВАНИЕМ" in v for v in _nz["validation"]))
check("И цена вопроса именно та, о которой предупреждаем",
      _nz["pl"]["margin_sources"]["base_activation"] >
      _z["pl"]["margin_sources"]["base_activation"] * 5,
      f'{_z["pl"]["margin_sources"]["base_activation"]:,} против '
      f'{_nz["pl"]["margin_sources"]["base_activation"]:,} ₽')
check("Ставка, которую не задавали, предупреждения не вызывает",
      not any("cashback_rate" in v for v in _z["validation"] if "ОБНУЛЕНА" in v),
      "иначе предупреждение сыпалось бы на каждом конфиге с нулевым кэшбэком")

# Регрессия: посегментный ключ утёк на уровень раздела и движок отверг конфиг.
# Форма «Допущения» рисует поля по общему списку разделов, и новый посегментный
# параметр туда попал — человек получал «неизвестный ключ audience.product_clients»
# на сохранении, не сделав ничего необычного.
_rg = int(client.post("/initiative/new", data={
    "name": "Общий уровень", "type": "product_launch", "product": "broker"},
    follow_redirects=False).headers["location"].split("/")[2])
client.post(f"/initiative/{_rg}/segments", data={
    "segments": ["teens"], "seg.teens.product_clients": "250000",
    "seg.teens.baseline_organic_apps_m": "0"}, follow_redirects=False)
_rgpage = client.get(f"/initiative/{_rg}/assumptions").text
check("Посегментного ключа нет в общей таблице параметров",
      'name="audience.product_clients"' not in _rgpage,
      "общего значения у него не бывает, движок такой ключ отвергает")
_rgr = client.post(f"/initiative/{_rg}/assumptions", data={
    "costs.campaign_months": "6", "ch.0.name": "Пуши", "ch.0.kind": "internal",
    "ch.0.frk_tier": "4", "ch.0.reach_abs": "3000000",
    "ch.0.response_rate": "0.08", "ch.0.cr_to_app": "0.25",
    "has_companion": "1", "companion.name": "рост активности",
    "companion.attach_rate": "0.5", "companion.avg_balance": "14000",
    "action": "save"}, follow_redirects=False)
check("Допущения со спутником сохраняются без ошибки движка",
      "error=" not in _rgr.headers.get("location", ""),
      unquote(_rgr.headers.get("location", ""))[:70])
check("И считаются",
      "calculation" in client.post(f"/initiative/{_rg}/calculate",
                                   follow_redirects=False).headers.get("location", ""))

# Проникновение продукта против проникновения банка (BACKLOG 59).
# На выводе продукта модель говорила «75% рынка уже наши, привлекать некого»,
# хотя брокерских счетов у подростков 250 тыс на 6 млн — то есть 4%.
_pi = int(client.post("/initiative/new", data={
    "name": "Проникновение", "type": "product_launch", "product": "broker"},
    follow_redirects=False).headers["location"].split("/")[2])
_spage = client.get(f"/initiative/{_pi}/segments").text
check("Поле «клиентов с этим продуктом» есть в форме сегментов",
      'name="seg.teens.product_clients"' in _spage,
      "без него проникновение продукта задать нечем")

client.post(f"/initiative/{_pi}/segments", data={
    "segments": ["teens"], "seg.teens.target_size": "6000000",
    "seg.teens.product_clients": "250000",
    "seg.teens.existing_clients": "4495500",
    "seg.teens.baseline_organic_apps_m": "0"}, follow_redirects=False)
_pcfg = json.loads(_db.get_initiative(_db.connect(), _pi)["config_json"])
_pseg = _pcfg["audience"]["segments"][0]
check("Клиенты продукта доезжают до конфига",
      _pseg.get("product_clients") == 250000.0)
check("Клиенты банка тоже — раньше поле рисовалось, а разбор его игнорировал",
      _pseg.get("existing_clients") == 4495500.0,
      "поле, которое показано и не читается, хуже отсутствующего")

client.post(f"/initiative/{_pi}/assumptions", data={
    "costs.campaign_months": "6", "ch.0.name": "Пуши", "ch.0.kind": "internal",
    "ch.0.frk_tier": "4", "ch.0.reach_abs": "3000000",
    "ch.0.response_rate": "0.02", "ch.0.cr_to_app": "0.25", "action": "save"},
    follow_redirects=False)
_pr = client.post(f"/initiative/{_pi}/calculate", follow_redirects=False)
_pres = json.loads(_db.get_calculation(
    _db.connect(), int(_pr.headers["location"].split("/")[-1]))["result_json"])
_pline = next((v for v in _pres["validation"] if "ПРОНИКНОВЕНИЕ" in v), "")
check("Считается проникновение ПРОДУКТА, а не банка",
      "ПРОНИКНОВЕНИЕ ПРОДУКТА" in _pline and "4.2%" in _pline,
      _pline[:90])
check("И вывод обратный прежнему: место для привлечения есть",
      "Место для привлечения есть" in _pline,
      "раньше говорилось «привлекать некого» при продукте у 4% рынка")
check("Насыщенность банка при этом названа отдельно, а не смешана",
      "клиентами банка охвачено" in _pline)

# без числа клиентов продукта — старое поведение, но с подсказкой, чего не хватает
_pi2 = int(client.post("/initiative/new", data={
    "name": "Без продукта", "type": "product_launch", "product": "broker"},
    follow_redirects=False).headers["location"].split("/")[2])
client.post(f"/initiative/{_pi2}/segments", data={
    "segments": ["teens"], "seg.teens.baseline_organic_apps_m": "0"},
    follow_redirects=False)
client.post(f"/initiative/{_pi2}/assumptions", data={
    "costs.campaign_months": "6", "ch.0.name": "Пуши", "ch.0.kind": "internal",
    "ch.0.frk_tier": "4", "ch.0.reach_abs": "3000000",
    "ch.0.response_rate": "0.02", "ch.0.cr_to_app": "0.25", "action": "save"},
    follow_redirects=False)
_pr2 = client.post(f"/initiative/{_pi2}/calculate", follow_redirects=False)
_pres2 = json.loads(_db.get_calculation(
    _db.connect(), int(_pr2.headers["location"].split("/")[-1]))["result_json"])
_pline2 = next((v for v in _pres2["validation"] if "ПРОНИКНОВЕНИЕ" in v), "")
check("Без числа клиентов продукта сказано, что это проникновение БАНКА",
      "ПРОНИКНОВЕНИЕ БАНКА" in _pline2 and "ЭТО НЕ ПРОНИКНОВЕНИЕ ПРОДУКТА" in _pline2,
      "и подсказано, какое поле заполнить")

# Каталог продуктов делится по единственному признаку, который меняет работу человека:
# есть готовая ценность клиента или экономику придётся собирать снизу вверх.
check("Список продуктов разделён на две группы",
      [g for g, _ in params.PRODUCT_GROUPS] and len(params.PRODUCT_GROUPS) == 2)
check("Деление выведено из наличия ценности, а не проставлено вторым полем",
      all(bool(params.PRODUCT_CATALOG[k].get("value")) == (i == 0)
          for i, (_, items) in enumerate(params.PRODUCT_GROUPS) for k, _ in items),
      "иначе каталог начнёт противоречить себе на первом же новом продукте")
check("Обе группы непусты и вместе покрывают весь каталог",
      sorted(k for _, items in params.PRODUCT_GROUPS for k, _ in items) ==
      sorted(params.PRODUCT_CATALOG))
check("Жаргона «родовая» в подписях продуктов не осталось",
      not any("родов" in label.lower() for label in params.PRODUCT_NAME.values()),
      "слово из систематики стояло прямо в выпадающем списке")

_npage = client.get("/initiative/new").text
check("Группы видны в самом списке, а не только в реестре",
      _npage.count("<optgroup") == 2 and
      all(g in _npage for g, _ in params.PRODUCT_GROUPS),
      "разница объясняется списком, и объяснять её словами уже не нужно")


# ------------------------------- Ложные предупреждения (BACKLOG 56)
# «ОШИБКА ВВОДА: размер ЦА = 0» на инициативе без привлечения, где ноль законен,
# и «Каннибализация = 0» там, где нового продукта нет. На потоке из ста идей человек
# приучается пролистывать блок проверки целиком — а там же лежат настоящие находки.
_n56 = json.loads(json.dumps(params.defaults()))
_n56["meta"].update(name="Фича", type="comms", horizon_months=24)
_n56["channels"] = []
_n56["audience"] = {"target_size": 0}
_n56["base_activation"] = {
    "name": "Раздел «Цели»", "eligible_clients": 4_000_000, "reach_share": 0.35,
    "technically_reachable": 0.9, "uplift_rate": 0.18, "attribution_share": 1.0,
    "turnover_before": 0, "turnover_after": 0, "balance_before": 0,
    "balance_after": 25000, "decay_months": 18}
_r56 = calc.analyze(calc.prepare(_n56))
_noise = [x for x in _r56["flags"] + _r56["validation"]
          if "ОШИБКА ВВОДА" in x or "Каннибализация = 0" in x]
check("У инициативы без привлечения нет ложных «ошибок ввода»",
      not _noise, "; ".join(n[:50] for n in _noise))
check("Но сама инициатива при этом считается",
      _r56["pl"]["npv"] != 0,
      "молчание не должно означать, что расчёт пустой")

# обратная сторона: там, где привлечение есть, оба предупреждения обязаны остаться
_c56 = json.loads((ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_c56["funnel"]["cannibalization"] = 0
for _sg in _c56["audience"]["segments"]:
    _sg["cannibalization"] = 0
check("На кампании с привлечением нулевая каннибализация окликается",
      any("Каннибализация = 0" in x for x in calc.analyze(calc.prepare(_c56))["flags"]))
_z56 = json.loads((ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_z56["audience"] = {**_z56["audience"], "target_size": 0, "segments": []}
check("И нулевая ЦА при заданных каналах — тоже",
      any("размер ЦА = 0" in x for x in calc.analyze(calc.prepare(_z56))["validation"]),
      "приглушать надо ложное, а не всё подряд")
