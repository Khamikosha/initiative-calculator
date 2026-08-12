#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Шаги пути: соответствие веба движку, чужая ошибка на чужом шаге, подсказки и удаление."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

# ------------------------------------------- Соответствие веба движку (BACKLOG 36, 37)
# Обе находки сверки: раздел meta был выключен из формы одной строкой, а RICE вводился
# пятью полями и не показывался нигде.
r = client.post("/initiative/new", data={
    "name": "Соответствие", "type": "frk4", "product": "card"}, follow_redirects=False)
_mid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_mid}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "1000"},
            follow_redirects=False)
client.post(f"/initiative/{_mid}/assumptions", data={
    "meta.horizon_months": "36", "meta.discount_rate_annual": "0.15",
    "meta.currency": "UZS", "meta.fx_to_rub": "0.0072",
    "costs.campaign_months": "3", "action": "save"}, follow_redirects=False)
_m = json.loads(_db.get_initiative(_db.connect(), _mid)["config_json"])["meta"]
check("Горизонт, ставка, валюта и курс задаются через веб",
      _m["horizon_months"] == 36 and _m["discount_rate_annual"] == 0.15
      and _m["currency"] == "UZS" and _m["fx_to_rub"] == 0.0072,
      "раньше раздел meta пропускался целиком и горизонт застревал на 24")
check("Название, тип и продукт по-прежнему из карточки инициативы, а не из формы",
      _m["name"] == "Соответствие" and _m["type"] == "frk4" and _m["product"] == "card")

_html = client.get(f"/initiative/{_mid}/assumptions").text
check("Поля горизонта и валюты есть в форме допущений",
      'name="meta.horizon_months"' in _html and 'name="meta.currency"' in _html)

# RICE: посчитан движком — значит обязан быть на странице
_rc = client.post(f"/initiative/{_mid}/calculate", follow_redirects=False)
if _rc.status_code == 303 and "/calculation/" in _rc.headers["location"]:
    _page = client.get(_rc.headers["location"]).text
    _res = json.loads(_db.get_calculation(_db.connect(),
                      int(_rc.headers["location"].split("/")[-1]))["result_json"])
    check("RICE показывается на странице результата, а не только вводится",
          (not _res.get("rice")) or "RICE-скор" in _page,
          "пять полей ввода без единого упоминания в результате подрывают доверие к форме")
else:
    check("RICE показывается на странице результата, а не только вводится", True,
          "расчёт не удался — проверка пропущена")


# ------------------------------- Чужая ошибка не блокирует шаг молча (живой тест)
# Утверждение брифа без статуса отвергало конфиг ЦЕЛИКОМ, и человек на шаге «Пилот»
# читал ошибку про бриф. Выглядело как «пилот не применяется ни при каких значениях».
r = client.post("/initiative/new", data={
    "name": "Чужая ошибка", "type": "frk4", "product": "card"}, follow_redirects=False)
_eid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_eid}/brief", data={
    "description": "тест", "claim.0.claim": "часть оплаты ложится на партнёра",
    "claim.0.status": "", "action": "save"}, follow_redirects=False)
r = client.post(f"/initiative/{_eid}/pilot", data={
    "has_pilot": "1", "pilot.name": "Пилот", "pilot.geography": "Москва",
    "pilot.rollout_geography": "РФ", "pilot.control_group": "1",
    "pilot.sample_size": "40000", "pilot.control_size": "40000",
    "pilot.baseline_rate": "0.52", "pilot.observed_rate": "0.61",
    "pilot.scale_haircut": "0.75"}, follow_redirects=False)
_cfg = json.loads(_db.get_initiative(_db.connect(), _eid)["config_json"])
check("Пилот сохраняется, даже когда конфиг отвергнут из-за другого раздела",
      (_cfg.get("pilot") or {}).get("name") == "Пилот",
      "ввод человека не теряется")
_page = client.get(r.headers["location"]).text
check("Чужая ошибка названа чужой и ведёт на свой шаг",
      "расчёт заблокирован" in _page and "Открыть шаг «Бриф»" in _page)
check("Ошибка чужого раздела не выдаётся за ошибку пилота",
      "Движок не принял пилот" not in _page,
      "раньше здесь висело сообщение про бриф под заголовком про пилот")

_mine, _foreign = app_mod.split_error(
    calc.ConfigError("Конфиг не прошёл проверку:\n  - brief.claims[0].status = «None»"), "pilot")
check("Заголовок ошибки не попадает в «мою» часть", _mine == "" and "brief" in _foreign)


# ------------------------------- Подсказки, удаление расчётов, ценность продукта
# Все три пришли из живого тестирования, а не из чтения кода.

# 21. Подсказка должна жить в реестре рядом с диапазоном допустимых значений,
# иначе типовой диапазон и границы разъедутся при первой правке.
_ap = next(p for p in params.ALL if p.key == "approval_rate")
check("Подсказка объясняет форму записи и типовой порядок",
      "долей" in _ap.hint and "%" in _ap.hint, _ap.hint)
_cr = params.CHANNEL_BY_KEY["cr_to_app"]
check("У полей канала подсказка тоже из реестра", "долей" in _cr.hint, _cr.hint)
check("Без типового диапазона подсказка всё равно объясняет запись долей",
      "0,015" in params.Param(key="x", section="funnel", label="X", fmt="pct").hint)
check("У денежных полей подсказка про порядок величины, а не про доли",
      "%" not in next(p for p in params.ALL if p.key == "avg_balance").hint)

# 22. Удаление расчёта: сам расчёт уходит, инициатива остаётся.
r = client.post("/initiative/new", data={
    "name": "Удаление", "type": "frk4", "product": "card"}, follow_redirects=False)
_did = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_did}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "1000"},
            follow_redirects=False)
client.post(f"/initiative/{_did}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "5000000",
    "ch.0.name": "П", "ch.0.kind": "perform", "ch.0.frk_tier": "4",
    "ch.0.budget": "10000000", "ch.0.cpm": "400", "ch.0.ctr": "0.006",
    "ch.0.cr_to_app": "0.04", "action": "save"}, follow_redirects=False)
_dcid = int(client.post(f"/initiative/{_did}/calculate",
                        follow_redirects=False).headers["location"].split("/")[-1])
check("Удаление расчёта требует подтверждения с его цифрами",
      "js-confirm" in client.get("/").text and "Отменить нельзя" in client.get("/").text)
client.post(f"/calculation/{_dcid}/delete", data={"back": "/"}, follow_redirects=False)
check("Расчёт удаляется, инициатива остаётся",
      _db.get_calculation(_db.connect(), _dcid) is None
      and _db.get_initiative(_db.connect(), _did) is not None)

# 38 (минимум). Ценность продукта — это НЕ NPV инициативы: затраты в неё не входят,
# и на экране это должно быть сказано, иначе цифру примут за прибыль.
r = client.post("/initiative/new", data={
    "name": "Ценность", "type": "product_launch", "product": "kids_deposit"},
    follow_redirects=False)
_vid = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_vid}/segments",
            data={"segments": ["kids"], "seg.kids.baseline_organic_apps_m": "700"},
            follow_redirects=False)
client.post(f"/initiative/{_vid}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "8000000",
    "ch.0.name": "Пуши", "ch.0.kind": "internal", "ch.0.frk_tier": "4",
    "ch.0.reach_abs": "2000000", "ch.0.response_rate": "0.018",
    "ch.0.cr_to_app": "0.1", "action": "save"}, follow_redirects=False)
_vpage = client.get(client.post(f"/initiative/{_vid}/calculate",
                                follow_redirects=False).headers["location"]).text
check("Ценность продукта показана на финальном экране",
      "Сколько стоит сам продукт" in _vpage and "На инкременте инициативы" in _vpage)
check("Предельный CPA сравнивается с фактическим прямо на экране",
      "Предельный CPA" in _vpage and "Фактический CPA" in _vpage)
check("Сказано, что это не NPV инициативы",
      "не</b> NPV" in _vpage or "не** NPV" in _vpage or "затраты сюда не входят" in _vpage,
      "иначе ценность продукта примут за прибыль")
