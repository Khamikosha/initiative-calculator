#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Блоки эффекта: связанный клиент, спутник, натуральный эффект, партнёр."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

# ------------------------------- Вложенные блоки эффекта в веб-форме (BACKLOG 20)
# Связанный клиент даёт больше половины эффекта на детских и семейных продуктах,
# а задать его можно было только конфигом — то есть самая денежная часть линейки
# была недоступна тем, ради кого приложение написано.
check("Поля вложенного блока берутся из реестра, а не из шаблона",
      {f.key for f in params.nested_fields("linked_client")} ==
      set(calc.LINKED_KEYS),
      "иначе четвёртый блок разойдётся с первыми тремя")
check("У полей вложенного блока есть подписи и пояснения",
      all(f.label != f.key and f.note for f in params.nested_fields("linked_client")
          if f.key != "sources"))
check("Заголовок раздела тоже из реестра",
      "Связанный клиент" in params.nested_title("linked_client"))

r = client.post("/initiative/new", data={
    "name": "Связанный", "type": "product_launch", "product": "kids_card"},
    follow_redirects=False)
_lid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_lid}/segments",
            data={"segments": ["kids"], "seg.kids.baseline_organic_apps_m": "700"},
            follow_redirects=False)
check("Блок связанного клиента есть в форме допущений",
      'name="has_linked_client"' in client.get(f"/initiative/{_lid}/assumptions").text)

_lc = {"has_linked_client": "1", "linked_client.name": "Родитель",
       "linked_client.clients_per_linked": "1.4", "linked_client.income_annual": "18000",
       "linked_client.margin_rate": "0.35", "linked_client.attribution_share": "0.4",
       "linked_client.churn_annual": "0.18", "linked_client.churn_reduction": "0.12",
       "linked_client.selection_haircut": "0.7", "linked_client.cross_sell_uplift": "0.05"}
client.post(f"/initiative/{_lid}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "8000000",
    "ch.0.name": "Пуши", "ch.0.kind": "internal", "ch.0.frk_tier": "4",
    "ch.0.reach_abs": "2000000", "ch.0.response_rate": "0.018",
    "ch.0.cr_to_app": "0.1", **_lc, "action": "save"}, follow_redirects=False)
_lcfg = json.loads(_db.get_initiative(_db.connect(), _lid)["config_json"])
check("Связанный клиент задаётся через веб и доезжает до конфига",
      (_lcfg.get("linked_client") or {}).get("income_annual") == 18000.0)

_lr = client.post(f"/initiative/{_lid}/calculate", follow_redirects=False)
_lres = json.loads(_db.get_calculation(
    _db.connect(), int(_lr.headers["location"].split("/")[-1]))["result_json"])
check("Эффект связанного клиента попадает в расчёт",
      (_lres["pl"].get("margin_sources") or {}).get("linked_client", 0) > 0,
      f'{(_lres["pl"].get("margin_sources") or {}).get("linked_client", 0):,} ₽')

# снятая галочка убирает блок целиком: полузаполненный даёт нулевой эффект
# и выглядит как «посчитали и ничего не вышло»
client.post(f"/initiative/{_lid}/assumptions", data={
    "costs.campaign_months": "3", "ch.0.name": "Пуши", "ch.0.kind": "internal",
    "ch.0.frk_tier": "4", "ch.0.reach_abs": "2000000",
    "ch.0.response_rate": "0.018", "ch.0.cr_to_app": "0.1", "action": "save"},
    follow_redirects=False)
check("Снятая галочка убирает блок целиком, а не оставляет полузаполненным",
      "linked_client" not in json.loads(
          _db.get_initiative(_db.connect(), _lid)["config_json"]))

# Остальные три блока эффекта. Раньше конфигом задавались все четыре, а веб знал
# один — то есть спутник, натуральный эффект и партнёрский P&L существовали
# в движке, но человеку через приложение были недоступны.
_NESTED = {"linked_client": calc.LINKED_KEYS, "companion": calc.COMPANION_KEYS,
           "natural_effect": calc.NATURAL_KEYS, "partner": calc.PARTNER_KEYS}
_page = client.get(f"/initiative/{_lid}/assumptions").text
for _sec, _keys in _NESTED.items():
    check(f"Блок «{params.nested_title(_sec)}»: поля реестра совпадают с движком",
          {f.key for f in params.nested_fields(_sec)} == set(_keys))
    check(f"Блок «{params.nested_title(_sec)}»: подписи и пояснения на месте",
          all(f.label != f.key and f.note for f in params.nested_fields(_sec)))
    # проверяем разметку, а не словарь: тест, не открывавший страницу,
    # ничего не знает о странице (BACKLOG 40)
    check(f"Блок «{params.nested_title(_sec)}» отрисован на странице допущений",
          f'name="has_{_sec}"' in _page)
    # структурные поля (корзины, доли, вычеты) в форму не помещаются — они помечены
    # в реестре как config_only, и шаблон больше не знает их имён поштучно
    _missing = [f.key for f in params.nested_fields(_sec)
                if not f.config_only and f'name="{_sec}.{f.key}"' not in _page]
    check(f"Блок «{params.nested_title(_sec)}»: все поля есть в разметке",
          not _missing, f"нет в форме: {_missing}" if _missing else
          f"{len(_keys)} полей")

# спутник — единственный блок со своей маржой поверх основного продукта,
# поэтому проверяем сквозняком: форма → конфиг → расчёт
client.post(f"/initiative/{_lid}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "8000000",
    "ch.0.name": "Пуши", "ch.0.kind": "internal", "ch.0.frk_tier": "4",
    "ch.0.reach_abs": "2000000", "ch.0.response_rate": "0.018",
    "ch.0.cr_to_app": "0.1", "has_companion": "1",
    "companion.name": "Копилка", "companion.attach_rate": "0.3",
    "companion.funded_rate": "0.6", "companion.avg_balance": "12000",
    "companion.nim_annual": "0.06", "companion.balance_growth_m": "0.02",
    "companion.spend_share": "0.1", "action": "save"}, follow_redirects=False)
_ccfg = json.loads(_db.get_initiative(_db.connect(), _lid)["config_json"])
check("Спутник задаётся через веб и доезжает до конфига",
      (_ccfg.get("companion") or {}).get("avg_balance") == 12000.0)
_cr = client.post(f"/initiative/{_lid}/calculate", follow_redirects=False)
_cres = json.loads(_db.get_calculation(
    _db.connect(), int(_cr.headers["location"].split("/")[-1]))["result_json"])
check("Маржа спутника попадает в расчёт отдельной строкой",
      (_cres["pl"].get("margin_sources") or {}).get("companion", 0) > 0,
      f'{(_cres["pl"].get("margin_sources") or {}).get("companion", 0):,.0f} ₽')


# RICE у инициативы без привлечения (BACKLOG 45). Инкремента там нет по построению —
# движок намеренно не кладёт растранзаченных в него, — и охват выходил нулём.
# Молча, у каждой фичи и каждой доработки, то есть ровно у того класса, ради
# приоритизации которого RICE и считают.
_fcfg = json.loads(json.dumps(params.defaults()))
_fcfg["meta"].update(name="Фича", type="comms", horizon_months=24)
_fcfg["channels"] = []
_fcfg["audience"] = {"target_size": 0}
_fcfg["base_activation"] = {
    "name": "Раздел «Цели»", "eligible_clients": 4_000_000, "reach_share": 0.35,
    "technically_reachable": 0.9, "uplift_rate": 0.18, "attribution_share": 1.0,
    "measured_against_control": 1, "turnover_before": 0, "turnover_after": 0,
    "balance_before": 0, "balance_after": 25000, "decay_months": 18}
_fcfg["rice"] = {"reach": 0, "impact": 2, "confidence": 0.7, "effort": 18,
                 "hurdle_ltv_cac": 3}
_fres = calc.analyze(calc.prepare(_fcfg))
check("RICE у инициативы без привлечения больше не ноль",
      _fres["rice"]["rice_score"] > 0,
      f'{_fres["rice"]["rice_score"]} при NPV {_fres["pl"]["npv"]:,}')
check("И сказано, откуда взят охват",
      "своя база" in (_fres["rice"].get("reach_source") or ""),
      _fres["rice"].get("reach_source"))
_acq = calc.analyze(calc.prepare(json.loads(
    (ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))))
check("У инициативы с привлечением охват по-прежнему из инкремента",
      "инкремент" in (_acq["rice"].get("reach_source") or ""))
_none = json.loads(json.dumps(_fcfg))
_none.pop("base_activation")
check("Когда охват вывести неоткуда — RICE не считается, а не равен нулю",
      calc.analyze(calc.prepare(_none))["rice"]["rice_score"] is None,
      "ноль читается как «низкий приоритет», а не как «мерить нечем»")

# Deadweight там, где за клиента платят (BACKLOG 47). Методичка объявляла его
# обязательным для акций, движок не проверял ни разу, а каннибализацию при нуле
# окликал всегда.
_dw = json.loads((ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_dw["costs"]["acq_cost_per_client"] = 500
_dw["funnel"]["deadweight"] = 0.0
check("Выплата за клиента при нулевом deadweight — флаг",
      any("DEADWEIGHT = 0" in x for x in calc.analyze(calc.prepare(_dw))["flags"]))
_dw2 = json.loads(json.dumps(_dw)); _dw2["funnel"]["deadweight"] = 0.35
check("Заданный deadweight флаг снимает",
      not any("DEADWEIGHT = 0" in x for x in calc.analyze(calc.prepare(_dw2))["flags"]))
_dw3 = json.loads(json.dumps(_dw)); _dw3["costs"]["acq_cost_per_client"] = 0
check("Без выплаты за клиента флага нет — это не общая придирка",
      not any("DEADWEIGHT = 0" in x for x in calc.analyze(calc.prepare(_dw3))["flags"]),
      "триггер по поведению конфига, а не по meta.type, который движок не читает")

# Подписи, противоречившие движку (BACKLOG 46)
_li = int(client.post("/initiative/new", data={
    "name": "Подписи", "type": "comms", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
_lbl = client.get(f"/initiative/{_li}/assumptions").text
check("Формулировки «на результат не влияют» больше нет",
      "на результат не влияют" not in _lbl,
      "движок meta.type не читает: ненулевое значение считается всегда")
check("И сказано прямо, что ненулевое значение считается наравне",
      "считается наравне с остальными" in _lbl)
check("«Без медиаплана считать нечего» больше не утверждается",
      "Без медиаплана считать нечего" not in _lbl,
      "инициатива без привлечения — законный расчёт, движок сам так её называет")

# Сценарии не различают инициативы на своей базе (BACKLOG 50).
_s50 = calc.analyze(calc.prepare(json.loads(
    (ROOT / "cases" / "ai_collections_youth.json").read_text("utf-8"))))
_npvs = {_s50["scenarios"][k]["npv"] for k in ("pessimistic", "base", "optimistic")}
check("Одинаковые сценарии распознаются как одинаковые",
      len(_npvs) == 1 and _s50["verdict"]["scenarios_flat"] is True,
      f"все три дали {_npvs.pop():,}")
check("Вердикт не выдаёт это за подтверждение тремя сценариями",
      "НЕ РАЗЛИЧАЮТ" in _s50["verdict"]["recommendation_by_npv"] and
      "во всех сценариях" not in _s50["verdict"]["recommendation_by_npv"],
      "«положительный NPV во всех сценариях» здесь означало «одно число трижды»")
check("И сказано, почему так вышло",
      any("СЦЕНАРИИ НЕ ТРОГАЮТ ДРАЙВЕРЫ СВОЕЙ БАЗЫ" in v for v in _s50["validation"]),
      "множители не касаются uplift растранзачивания и монетизации")
_s50b = calc.analyze(calc.prepare(json.loads(
    (ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))))
check("У обычной инициативы сценарии по-прежнему различаются",
      _s50b["verdict"]["scenarios_flat"] is False and
      "НЕ РАЗЛИЧАЮТ" not in _s50b["verdict"]["recommendation_by_npv"])

# Каталог: значения ценности клиента приходят от заказчика и обязаны доезжать целиком
check("Ценность детской карты — 4 424 ₽",
      params.PRODUCT_CATALOG["kids_card"]["value"]["kids"] == 4424)
check("Молодёжная кредитная карта в каталоге, с 18 лет и своей ценностью",
      params.PRODUCT_CATALOG["youth_credit_card"]["value"]["youth"] == 21285 and
      calc.PRODUCT_AGE["youth_credit_card"][0] == 18)
check("Кредитная карта помечена кредитной — иначе CoR не спросят",
      "youth_credit_card" in calc.CREDIT_PRODUCTS,
      "без этого Cost of Risk = 0 прошёл бы без единого слова")
_cc = params.segment_defaults("youth", "youth_credit_card")
check("Подросткам кредитная карта недоступна",
      params.segment_defaults("teens", "youth_credit_card") is None and _cc is not None)
check("Новый продукт попал в группу с готовой ценностью",
      "youth_credit_card" in {k for k, _ in params.PRODUCT_GROUPS[0][1]})

# ROMI и LTV на экране результата (BACKLOG 48, 49, 57).
_r48 = json.loads((ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_r48["costs"]["it_dev"] = 500_000_000
_res48 = calc.analyze(calc.prepare(_r48))
check("Рядом с ROMI считается отдача ко всем затратам",
      _res48["pl"]["roi_total"] is not None and
      _res48["pl"]["roi_total"] < _res48["pl"]["romi"],
      f'ROMI {_res48["pl"]["romi"]} против {_res48["pl"]["roi_total"]} по всем затратам')
check("Доля маркетинга в затратах названа числом",
      _res48["pl"]["marketing_share_of_costs"] < 0.2)
check("И когда маркетинг — меньшая часть затрат, об этом сказано флагом",
      any("ROMI СЧИТАЕТСЯ НЕ КО ВСЕМ" in x for x in _res48["flags"]),
      "IT на 500 млн роняет NPV в двенадцать раз, ROMI не двигает вовсе")
_r48b = json.loads(json.dumps(_r48)); _r48b["costs"]["it_dev"] = 0
check("При обычной структуре затрат флага нет",
      not any("ROMI СЧИТАЕТСЯ НЕ КО ВСЕМ" in x
              for x in calc.analyze(calc.prepare(_r48b))["flags"]))

# LTV — ценность привлечённого клиента, поэтому маржа действующей базы в неё не входит
_l49 = json.loads((ROOT / "cases" / "youth_card_launch_2026.json").read_text("utf-8"))
_with_ba = calc.analyze(calc.prepare(json.loads(json.dumps(_l49))))["pl"]
_no_ba = json.loads(json.dumps(_l49)); _no_ba.pop("base_activation", None)
_wo_ba = calc.analyze(calc.prepare(_no_ba))["pl"]
check("Растранзачивание больше не завышает LTV привлечённого клиента",
      _with_ba["ltv_per_client"] == _wo_ba["ltv_per_client"],
      f'{_with_ba["ltv_per_client"]:,} ₽ в обоих случаях — было завышение на 30%')
check("И не завышает предельный CPA с допустимым бюджетом",
      _with_ba["max_cpa"] == _wo_ba["max_cpa"] and
      _with_ba["budget_at_max_cpa"] == _wo_ba["budget_at_max_cpa"],
      "именно на этот вопрос блок и отвечает: на какой бюджет можно идти")
check("Маржа привлечённого клиента отделена от общей",
      _with_ba["acquired_gross_margin"] < _with_ba["total_gross_margin"],
      "спутник и связанный клиент входят, растранзачивание и натуральный эффект — нет")

# Плитки: у инициативы без привлечения четыре из шести были прочерками
_t57 = int(client.post("/initiative/new", data={
    "name": "Фича без привлечения", "type": "comms", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
client.post(f"/initiative/{_t57}/assumptions", data={
    "costs.campaign_months": "6", "has_base_activation": "1",
    "base_activation.name": "Раздел «Цели»",
    "base_activation.eligible_clients": "4000000",
    "base_activation.reach_share": "0.35",
    "base_activation.technically_reachable": "0.9",
    "base_activation.uplift_rate": "0.18",
    "base_activation.attribution_share": "1",
    "base_activation.balance_after": "25000",
    "base_activation.decay_months": "18", "action": "save"}, follow_redirects=False)
_tr = client.post(f"/initiative/{_t57}/calculate", follow_redirects=False)
_thtml = client.get(_tr.headers["location"]).text
# ищем ТОЛЬКО в блоке плиток: «Инкрементальный CPA» есть ещё и в таблице сценариев
# блок плиток — от его начала до первой карточки-секции ниже
_tiles = _thtml.split('<div class="tiles">')[1].split('<section class="card">')[0]
check("У инициативы без привлечения плитки привлечения не показаны",
      "Инкрементальный CPA" not in _tiles and "LTV / CAC" not in _tiles,
      "четыре плитки из шести были прочерками или искажены")
check("И сказано, почему их нет",
      "метрики привлечения" in _thtml and "не считаются и здесь не показаны" in _thtml)
check("NPV при этом на месте",
      "NPV" in _thtml and "Чистый эффект" in _thtml)
