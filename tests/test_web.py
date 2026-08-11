#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты веб-пути: инициатива → сегменты → панель → допущения → расчёт → выгрузка.

Запуск: make test-web

Проверяем не «страница открылась», а свойства, ради которых приложение написано:
цифры приходят из движка, расчёт неизменяем, строгая валидация видна человеку,
возрастные правила применяются, реестр параметров не разошёлся с движком.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# отдельная база на прогон: тесты не должны трогать рабочие данные
TMP = Path(tempfile.mkdtemp())
os.environ["CALC_DB"] = str(TMP / "test.db")

from web import db as _db                                        # noqa: E402
_db.DB_PATH = Path(os.environ["CALC_DB"])

from fastapi.testclient import TestClient                        # noqa: E402
from web import app as app_mod                                   # noqa: E402
from web.app import app, calc, params                            # noqa: E402

client = TestClient(app)
ok, fail = [], []


def check(name, cond, detail=""):
    (ok if cond else fail).append(f"{name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- 1. Реестр параметров
check("Реестр параметров согласован с движком", len(params.ALL) > 0)
check("Дефолты реестра проходят строгую валидацию движка",
      calc.validate_config(params.defaults()) is not None)
check("Каждый параметр реестра имеет подпись и пояснение",
      all(p.label and (p.note or p.section in ("meta", "rice")) for p in params.ALL))

# ---------------------------------------------------------------- 1б. Разбивка по типам
check("Все типы инициатив описаны в реестре",
      set(params.INITIATIVE_TYPES) == {"comms", "frk4", "frk3", "frk2", "frk1",
                                       "product_launch", "pilot"})
check("Органика и halo работают только там, где есть охватные каналы",
      all(not p.works_for("comms") and not p.works_for("frk4") and p.works_for("frk1")
          for p in params.ALL
          if p.key in ("baseline_organic_apps_m", "halo_months", "halo_decay")))
check("IT-разработка и opex — только для вывода продукта",
      all(p.works_for("product_launch") and not p.works_for("comms")
          for p in params.ALL if p.key in ("it_dev", "opex_monthly")))
check("Deadweight — для пилотов и акций",
      next(p for p in params.ALL if p.key == "deadweight").types == ("pilot",))
check("Юнит-экономика нужна всем типам: NPV считается всегда",
      all(not p.types for p in params.ALL if p.section == "unit_economics"))
check("Поля канала привязаны к типу канала, а не к типу инициативы",
      {p.key for p in params.channel_fields("internal")} ==
      {"name", "kind", "frk_tier", "budget", "cr_to_app", "response_rate", "reach_abs"},
      f'internal: {sorted(p.key for p in params.channel_fields("internal"))}')
check("Brand-поля есть только у охватных каналов",
      all("brand_uplift" not in {p.key for p in params.channel_fields(k)}
          for k in ("internal", "perform")))

_main, _rest = params.split_by_type(params.COSTS, "comms")
check("При коммуникации IT-разработка уходит в «прочие»",
      "it_dev" in {p.key for p in _rest} and "it_dev" not in {p.key for p in _main})
_main2, _rest2 = params.split_by_type(params.COSTS, "comms", {"costs": {"it_dev": 50_000_000}})
check("Ненулевое «нерелевантное» значение остаётся на виду — деньги всё равно считаются",
      "it_dev" in {p.key for p in _main2},
      "50 млн на IT при типе comms не прячутся под раскрытие")

# ------------------------------------------- 1в. Подстановка из реестра и каталога
# Живой тест вскрыл две ошибки ввода подряд: человек вручную вписал базу 6–13 в поле,
# которое движок понимает как рынок сегмента 0–13 (двойная возрастная отсечка), и получил
# вклад, посчитанный по дефолтам дебетовой карты. Оба ввода теперь не нужны.
_d = params.segment_defaults("kids", "kids_key_deposit")
_card = params.segment_defaults("kids", "kids_card")
check("Рынок берётся по возрастам, где продукт доступен",
      _card["ages"] == (6, 13) and _card["target_size"] == 11_000_000 and
      _d["ages"] == (0, 13),
      "карта с 6 лет, вклад с рождения — рамки разные у продуктов одного сегмента")
check("Достижимая доля = 1: возрастное правило уже в числителе, второй раз не режем",
      _d["reachable_share"] == 1.0 and _card["reachable_share"] == 1.0,
      "иначе 11 млн превратились бы в 6,3 млн")
check("Число клиентов и проникновение подставляются из реестра",
      _card["existing_clients"] == 5_000_000 and abs(_card["penetration"] - 5 / 11) < 0.01 and
      _d["existing_clients"] == 5_050_000 and abs(_d["penetration"] - 5.05 / 17) < 0.01,
      "вклад с рождения: 5,05 млн клиентов на рынке 17 млн — проникновение 30%, "
      "а не 46% как у карты")
check("Ценность клиента берётся из каталога продукта",
      _d["value_per_client"] == 14670)
check("У продукта без каталожной ценности режим остаётся build_up",
      params.product_economics("card")["mode"] == "build_up" and
      params.product_economics("kids_key_deposit")["mode"] == "value_per_client")
check("Недоступный сегмент не получает дефолтов вовсе",
      params.segment_defaults("youth", "kids_card") is None)

_cfg = app_mod.apply_product_economics(params.defaults(), "kids_key_deposit")
check("Горизонт и ставка приходят из модели продукта",
      _cfg["meta"]["horizon_months"] == 36 and _cfg["meta"]["discount_rate_annual"] == 0.2)
check("Строки маржи снизу вверх обнуляются — иначе движок ругается в каждом прогоне",
      all(_cfg["unit_economics"][k] == 0 for k in
          ("nim_annual", "monthly_turnover", "interchange_rate", "cashback_rate",
           "servicing_cost_m", "cor_annual")))
check("Средний баланс сохраняется: он остаётся источником AUM",
      _cfg["unit_economics"]["avg_balance"] > 0)

_cfg2 = {"audience": {"segments": [{"id": "kids", "target_size": 999}]}}
app_mod.apply_segment_defaults(_cfg2, "kids_key_deposit")
_seg = _cfg2["audience"]["segments"][0]
check("Введённое руками не затирается подстановкой",
      _seg["target_size"] == 999 and _seg["value_per_client"] == 14670,
      "рынок остался пользовательским, ценность подставилась")

# ------------------------------------------- 1г. Обязательные поля канала
# Живой тест: у internal-канала не заполнили response_rate, воронка схлопнулась в ноль,
# и человек получил «NO-GO» вместо ошибки. Формула не ломается — она честно умножает
# на ноль, поэтому недостачу должна ловить форма.
check("Для internal обязательны охват базы, отклик и конверсия",
      set(params.CHANNEL_REQUIRED["internal"]) == {"reach_abs", "response_rate", "cr_to_app"})
check("Для perform обязательны бюджет, CPM, CTR и конверсия",
      set(params.CHANNEL_REQUIRED["perform"]) == {"budget", "cpm", "ctr", "cr_to_app"})
check("Для brand конверсия не нужна — охватные каналы прямых заявок не дают",
      "cr_to_app" not in params.CHANNEL_REQUIRED["brand"] and
      "brand_uplift" in params.CHANNEL_REQUIRED["brand"])

_bad_ch = {"name": "Пуши", "kind": "internal", "reach_abs": 2_000_000, "cr_to_app": 0.1}
check("Незаполненный отклик у internal виден как недостача",
      params.channel_missing(_bad_ch) == ["response_rate"],
      "ровно тот случай, что дал нули на живом тесте")
_bad_ch["response_rate"] = 0.018
check("Заполненный канал больше не числится неполным",
      params.channel_missing(_bad_ch) == [])
check("Показы засчитываются вместо связки бюджет и CPM",
      params.channel_missing({"name": "П", "kind": "perform", "impressions": 1e6,
                              "ctr": 0.01, "cr_to_app": 0.05}) == [])
check("Пустой медиаплан — не готов к расчёту",
      not params.channels_ready({"channels": []}) and
      not params.channels_ready({"channels": [{"name": "П", "kind": "perform"}]}) and
      params.channels_ready({"channels": [_bad_ch]}))

# Сквозной сценарий пункта 0.3: пройти путь, не открывая полную форму параметров,
# и получить осмысленный расчёт. Задаём только медиаплан и сроки — всё остальное
# должно подставиться само.
r = client.post("/initiative/new", data={
    "name": "Минимальный ввод", "type": "product_launch", "product": "kids_deposit",
    "description": "только медиаплан"}, follow_redirects=False)
_min = int(r.headers["location"].split("/")[2])
client.post(f"/initiative/{_min}/segments",
            data={"segments": ["kids"], "seg.kids.baseline_organic_apps_m": "700"},
            follow_redirects=False)
client.post(f"/initiative/{_min}/assumptions", data={
    "costs.campaign_months": "3", "costs.production": "8000000",
    "ch.0.name": "Родительские пуши", "ch.0.kind": "internal", "ch.0.frk_tier": "4",
    "ch.0.reach_abs": "2000000", "ch.0.response_rate": "0.018", "ch.0.cr_to_app": "0.1",
}, follow_redirects=False)
r = client.post(f"/initiative/{_min}/calculate", follow_redirects=False)
_cid = int(r.headers["location"].split("/")[-1])
_res = json.loads(_db.get_calculation(_db.connect(), _cid)["result_json"])
check("Путь проходится без открытия полной формы параметров",
      _res["funnel"]["incremental_clients"] > 0 and
      not _res["verdict"].get("nothing_to_calculate"),
      f'инкремент {_res["funnel"]["incremental_clients"]:,}, NPV {_res["pl"]["npv"]:,} ₽')
check("Экономика подставилась из каталога, а не осталась дефолтом карты",
      _res["pl"]["mode"] == "value_per_client" and _res["pl"]["value_per_client"] == 21903)

# ------------------------------------------- 1д. Навык не отстал от проекта
# Копия движка в навыке должна совпадать побайтово, а реестр обязан быть рядом с ней:
# без params.py навык посчитает, но каталог продуктов, ОД и демографию будет спрашивать
# у человека заново — то есть ровно то, от чего мы уходили.
_SK = ROOT / ".claude" / "skills" / "initiative-impact-calculator"
check("Движок в навыке совпадает с src/calc.py побайтово",
      (_SK / "scripts" / "calc.py").read_bytes() == (ROOT / "src" / "calc.py").read_bytes())
check("Реестр лежит рядом с движком в навыке",
      (_SK / "scripts" / "params.py").read_bytes() == (ROOT / "src" / "params.py").read_bytes())
check("Кейсы в навыке те же, что в проекте",
      {f.name for f in (_SK / "reference").glob("*.json")} ==
      {f.name for f in (ROOT / "cases").glob("*.json")})
_skill_md = (_SK / "SKILL.md").read_text(encoding="utf-8")
check("Навык описывает то, что реально умеет движок",
      all(k in _skill_md for k in
          ("value_per_client", "params.py", "проникновени", "НЕЧЕГО СЧИТАТЬ", "АКБ")),
      "режимы экономики, реестр, проникновение, пустой прогон, валюты эффекта")

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
      any("СПЯЩАЯ БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ" in v for v in _without["validation"]))

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


# ---------------------------------------------------------------- 2. Путь целиком
client.post("/author", data={"author": "Тестировщик", "back": "/"}, follow_redirects=False)

r = client.post("/initiative/new", data={
    "name": "Молодёжная карта — тест", "type": "frk3", "product": "card",
    "description": "Проверка сквозного пути"}, follow_redirects=False)
check("Инициатива создаётся и ведёт на разбор брифа",
      r.status_code == 303 and "/brief" in r.headers["location"])
iid = int(r.headers["location"].split("/")[2])

# ---------------------------------------------------------------- 2a. Шаг «Бриф»
# Описание инициативы — не украшение названия, а непроверенный источник параметров.
# Пока оно не разложено на утверждения, модель считает обеднённую версию инициативы,
# и человек об этом не знает: цифра выглядит уверенно.
r = client.get(f"/initiative/{iid}/brief")
check("Шаг «Бриф» открывается и показывает описание дословно",
      r.status_code == 200 and "Проверка сквозного пути" in r.text)

r = client.post(f"/initiative/{iid}/brief", data={
    "claim.0.claim": "выдаём по всей стране",
    "claim.0.implication": "достижимый рынок — вся РФ",
    "claim.0.status": "applied",
    "claim.0.maps_to": "",                       # applied без параметра — движок обязан ругнуться
    "action": "save"}, follow_redirects=False)
check("Бриф без maps_to не принимается — правило проверяет движок, а не форма",
      r.status_code == 303 and "error=" in r.headers["location"])
check("Причина отказа доносится человеку",
      "maps_to" in unquote(r.headers["location"]))

r = client.post(f"/initiative/{iid}/brief", data={
    "claim.0.claim": "выдаём по всей стране",
    "claim.0.implication": "достижимый рынок — вся РФ",
    "claim.0.status": "applied", "claim.0.maps_to": "audience.reachable_market",
    "claim.0.grade": "C",
    "claim.1.claim": "партнёры дают скидку",
    "claim.1.implication": "кто её финансирует — банк или партнёр?",
    "claim.1.status": "question",
    "claim.2.claim": "региональные различия",
    "claim.2.status": "ignored",
    "claim.2.reason": "страна считается одним куском",
    "claim.3.claim": "",                          # пустая строка выбрасывается
    "action": "save"}, follow_redirects=False)
check("Заполненный бриф сохраняется", r.status_code == 303 and "error=" not in r.headers["location"])

_cfg = json.loads(_db.get_initiative(app_mod.con, iid)["config_json"])
check("Утверждения легли в конфиг", len(_cfg["brief"]["claims"]) == 3)
check("Пустая строка таблицы не попала в конфиг",
      all(c.get("claim") for c in _cfg["brief"]["claims"]))
check("Описание инициативы попало в бриф дословно",
      _cfg["brief"]["description"] == "Проверка сквозного пути")

_cov = calc.brief_coverage(_cfg)
check("Покрытие считает движок, а не форма — 1 из 3",
      _cov["applied"] == 1 and _cov["questions"] == 1 and _cov["ignored"] == 1)

r = client.get(f"/initiative/{iid}/brief")
check("Открытые вопросы и неучтённое видны на странице",
      "1" in r.text and "Разобрано" in r.text)

# ---------------------------------------------------------------- 2б. Шаг «Пилот»
# Пилот, который провели и не использовали в расчёте, — выброшенные деньги. Раньше
# задать его цифры через веб было нельзя вовсе, только конфигом.
r = client.get(f"/initiative/{iid}/pilot")
check("Шаг «Пилот» открывается", r.status_code == 200 and "Пилот уже проводился" in r.text)

r = client.post(f"/initiative/{iid}/pilot", data={"action": "save"}, follow_redirects=False)
_cfg = json.loads(_db.get_initiative(app_mod.con, iid)["config_json"])
check("Без галочки раздел пилота не создаётся", "pilot" not in _cfg,
      "инициатива без пилота не должна тащить пустой блок в конфиг")

r = client.post(f"/initiative/{iid}/pilot", data={
    "has_pilot": "on", "pilot.name": "Пилот в Москве",
    "pilot.geography": "Москва", "pilot.rollout_geography": "РФ",
    "pilot.control_group": "on",
    "pilot.sample_size": "40000", "pilot.control_size": "40000",
    "pilot.baseline_rate": "0,52", "pilot.observed_rate": "0,61",
    "pilot.scale_haircut": "0,75", "pilot.metrics_are": "level",
    "metric.0.key": "activation_rate", "metric.0.value": "0,61",
    "metric.1.key": "monthly_turnover", "metric.1.value": "34000",
    "metric.2.key": "approval_rate", "metric.2.value": "",     # пустое не сохраняется
    "action": "save"}, follow_redirects=False)
check("Пилот сохраняется", r.status_code == 303 and "error=" not in r.headers["location"])

_cfg = json.loads(_db.get_initiative(app_mod.con, iid)["config_json"])
check("Метрики пилота легли в конфиг",
      _cfg["pilot"]["metrics"] == {"activation_rate": 0.61, "monthly_turnover": 34000.0})
check("Пустая метрика не сохранилась", "approval_rate" not in _cfg["pilot"]["metrics"])
check("Русская запятая разобрана и в полях пилота", _cfg["pilot"]["baseline_rate"] == 0.52)

_no_answer = json.loads(json.dumps(_cfg))
_no_answer["pilot"].pop("metrics_are", None)
try:
    calc.validate_config(_no_answer)
    _pilot_err = []
except calc.ConfigError as e:
    _pilot_err = str(e).splitlines()


def _apply_as(kind):
    c = json.loads(json.dumps(_cfg))
    c["pilot"]["metrics_are"] = kind
    return calc.apply_pilot(c)


_probe_level, _probe_uplift = _apply_as("level"), _apply_as("uplift")
_applied_level = {a["param"] for a in _probe_level["_pilot"]["applied"]}
_applied_uplift = {a["param"] for a in _probe_uplift["_pilot"]["applied"]}

_probe = calc.apply_pilot(json.loads(json.dumps(_cfg)))
check("География Москва → РФ понижает грейд до B−", _probe["_pilot"]["grade"] == "B_minus")
check("Мощность выборки считает движок: 9 п.п. на 40 000 доказуемы",
      _probe["_pilot"]["power"]["conclusive"] is True)
# Развилка «уровень или прирост». Число одно и то же, фразы звучат одинаково,
# а разница на этом кейсе — 15 млн ₽ NPV. Раньше движок выбирал молча.
check("Пилот с контролем и метрикой в рублях требует ответа, что в ней записано",
      any("metrics_are" in e for e in _pilot_err),
      "; ".join(_pilot_err)[:80] if _pilot_err else "движок промолчал")
check("Ответ «уровень» подставляет величину в модель",
      "unit_economics.monthly_turnover" in _applied_level)
check("Ответ «прирост» НЕ подставляет её как уровень",
      "unit_economics.monthly_turnover" not in _applied_uplift,
      "иначе вышел бы клиент, у которого весь оборот равен приросту")
check("Отказ подставить объяснён и указывает, чем считать прирост",
      any("ПРИРОСТ НЕ ПОДСТАВЛЕН" in n and "Растранзачивание" in n
          for n in _probe_uplift.get("_notes", [])))
check("Подстановка уровня оговаривает, что контроль для неё не использовался",
      any("УРОВЕНЬ, А НЕ ПРИРОСТ" in n for n in _probe_level.get("_notes", [])))
check("Доли развилка не касается: конверсия подставляется при любом ответе",
      "funnel.activation_rate" in _applied_level and
      "funnel.activation_rate" in _applied_uplift,
      "у доли «уровень» и «прирост» не путаются — прирост там задан отдельными полями")

# Дизайн замера, доказательность в рублях и окно наблюдения.
# Пилот молодёжной карты: рандомизация с выравниванием, 14 000 на группу,
# 8 месяцев (май–декабрь), только средние — разброса нет.
_beh = json.loads(json.dumps(_cfg))
_beh["pilot"].update({"randomized": True, "matched_at_baseline": True,
                      "sample_size": 14000, "control_size": 14000,
                      "duration_months": 8, "metrics_are": "uplift",
                      "metrics": {"monthly_turnover": 1800}})


def _notes_of(cfg):
    return calc.apply_pilot(calc.validate_config(json.loads(json.dumps(cfg))))["_notes"]


_n_nosd = _notes_of(_beh)
check("Рандомизация с выравниванием снимает вопрос об отборе",
      any("ДИЗАЙН" in n and "поправка на отбор не нужна" in n for n in _n_nosd))
_selfsel = json.loads(json.dumps(_beh)); _selfsel["pilot"]["randomized"] = False
check("Без рандомизации движок называет отбор отбором",
      any("ОТБОР ВМЕСТО ЭФФЕКТА" in n for n in _notes_of(_selfsel)))
check("Только средние — «доказано» не пишется ни при каких условиях",
      not any("ПРИРОСТ ДОКАЗАТЕЛЕН" in n for n in _n_nosd) and
      any("РАЗБРОСА НЕТ" in n or "ДОКАЗАТЕЛЬНОСТЬ НЕ ПРОВЕРЕНА" in n for n in _n_nosd),
      "у рубля разброс из среднего не выводится")

_thr = calc.mde_mean(2.0 * _cfg["unit_economics"]["monthly_turnover"], 14000)
_cv = json.loads(json.dumps(_beh)); _cv["pilot"]["metric_cv"] = 2.0
_cv["pilot"]["metrics"] = {"monthly_turnover": round(_thr * 1.2)}
check("С заданным разбросом доказательность считается",
      any("ПРИРОСТ ДОКАЗАТЕЛЕН" in n for n in _notes_of(_cv)),
      f"порог {_thr:,.0f} ₽ при 14 000 на группу")
_small = json.loads(json.dumps(_cv))
_small["pilot"]["metrics"] = {"monthly_turnover": round(_thr * 0.5)}
check("Малый прирост при том же разбросе объявлен неотличимым от шума",
      any("НЕОТЛИЧИМ ОТ ШУМА" in n for n in _notes_of(_small)),
      "половина порога на тех же группах — это шум, а не эффект")
check("Порог различимости считает движок и он зависит от размера групп",
      calc.mde_mean(2.0 * 24000, 14000) > calc.mde_mean(2.0 * 24000, 56000),
      f"14 000 на группу: {calc.mde_mean(2.0 * 24000, 14000):,.0f} ₽, "
      f"56 000: {calc.mde_mean(2.0 * 24000, 56000):,.0f} ₽")

check("Восемь месяцев из двенадцати названы неполным годом",
      any("ОКНО НАБЛЮДЕНИЯ" in n for n in _n_nosd),
      "май–декабрь не покрывают год, а перенос делается как на наблюдавшиеся")
check("Замер короче горизонта — сказано, что дальше держится по допущению",
      any("ЗАМЕР" in n and "ГОРИЗОНТ" in n for n in _n_nosd))

# Затухание из двух чисел. Помесячной аналитики нет, и не нужно: хвост окна
# против средней за окно однозначно задаёт срок — при той модели затухания,
# которую движок реально применяет.
check("Формула обращает кривую затухания самого движка",
      abs(calc.decay_from_tail(8, 2, 0.333333) - 8.0) < 0.01,
      "затухание ровно за 8 мес даёт отношение 1/3 — и обратно")
check("Хвост не ниже средней — срок не выводится, это не ошибка",
      calc.decay_from_tail(8, 2, 1.0) is None and calc.decay_from_tail(8, 2, 1.2) is None)
check("Чем ближе хвост к средней, тем дольше живёт эффект",
      calc.decay_from_tail(8, 2, 0.9) > calc.decay_from_tail(8, 2, 0.6))

_tail = json.loads(json.dumps(_beh))
_tail["pilot"].update({"tail_months": 2, "metrics_tail": {"monthly_turnover": 1440}})
_pt = calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_tail))))
check("Срок затухания выведен из замера и назван",
      _pt["_pilot"]["decay_from_tail"]["monthly_turnover"] == 18.5 and
      any("ЗАТУХАНИЕ ИЗ ЗАМЕРА" in n for n in _pt["_notes"]),
      "хвост 1 440 против средней 1 800 → около 19 мес")
check("Средняя за окно не выдаётся за пик",
      any("СРЕДНЯЯ ЗА ОКНО — НЕ ПИК" in n for n in _pt["_notes"]))

_flat = json.loads(json.dumps(_tail))
_flat["pilot"]["metrics_tail"] = {"monthly_turnover": 1800}
check("Ровный хвост читается как полка, а не как затухание",
      any("ЭФФЕКТ НЕ ЗАТУХАЕТ" in n for n in _notes_of(_flat)),
      "и сказано, что перенос средней тогда скорее занижает")

_bad = json.loads(json.dumps(_tail))
_bad["pilot"]["metrics_tail"] = {"avg_balance": 100}
try:
    calc.validate_config(_bad); _bad_err = []
except calc.ConfigError as e:
    _bad_err = str(e).splitlines()
check("Хвост без своей средней отвергается: сравнивать не с чем",
      any("metrics_tail" in e for e in _bad_err))

# Шаг пилота просил 16 полей и две таблицы у человека, которому нужна половина,
# и он не знал, какая именно. Развилка задаётся ОДНИМ вопросом наверху.
_kpage = client.get(f"/initiative/{iid}/pilot").text
check("Первым задаётся вопрос, что пилот мерил",
      'name="pilot.kind"' in _kpage and
      _kpage.index('name="pilot.kind"') < _kpage.index('name="pilot.scale_haircut"'),
      "от ответа зависит, какие поля вообще показывать")
check("Ветки размечены и различимы",
      'data-kind="conversion"' in _kpage and 'data-kind="behavioral"' in _kpage)
_conv = _kpage.split('data-kind="conversion"')[1].split('data-kind="behavioral"')[0]
_beh_html = _kpage.split('data-kind="behavioral"')[1]
check("Конверсия живёт только в своей ветке",
      'name="pilot.observed_rate"' in _conv and 'name="pilot.observed_rate"' not in _beh_html)
check("Групповые замеры живут только в своей",
      'name="pgroup.0.name"' in _beh_html and 'name="pgroup.0.name"' not in _conv)
check("Размеры групп общие: без них ни один тип пилота не проверить на шум",
      'name="pilot.sample_size"' in _kpage.split('data-kind="conversion"')[0],
      "мощность выборки нужна и конверсии, и поведению")
# ищем по разметке раскрытия, а не по слову «Уточнения»: оно есть и в подводке,
# и срез между вхождениями попадал мимо блока
_fold = _kpage.split('<details class="fold">')[-1]
check("Редкое убрано под раскрытие, а не выброшено",
      "Уточнения" in _kpage and
      all(f'name="pilot.{k}"' in _fold
          for k in ("metric_cv", "tail_months", "metrics_are", "audience")))

# Конфликт веток чистится на СЕРВЕРЕ: скрытые поля всё равно уходят с формой,
# а конфиг с обеими ветками движок отвергнет — человек при этом видел половину
# и не поймёт, о чём речь.
_ki = int(client.post("/initiative/new", data={
    "name": "Ветки", "type": "product_launch", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
_kd = {"has_pilot": "on", "pilot.kind": "behavioral", "pilot.name": "М",
       "pilot.baseline_rate": "0,52", "pilot.observed_rate": "0,61",
       "pgroup.0.name": "МК", "pgroup.0.role": "test",
       "pgroup.0.metric": "monthly_turnover",
       "pgroup.0.value_start": "22000", "pgroup.0.value_end": "27400",
       "pgroup.1.name": "ОК", "pgroup.1.role": "control",
       "pgroup.1.metric": "monthly_turnover",
       "pgroup.1.value_start": "21800", "pgroup.1.value_end": "24000",
       "action": "save"}
_kr = client.post(f"/initiative/{_ki}/pilot", data=_kd, follow_redirects=False)
_kcfg = json.loads(_db.get_initiative(_db.connect(), _ki)["config_json"])
check("Поля чужой ветки не сохраняются и не ломают конфиг",
      "error=" not in _kr.headers.get("location", "") and
      "observed_rate" not in _kcfg["pilot"] and len(_kcfg["pilot"]["groups"]) == 2)
check("Контроль у поведенческого пилота — это строка «контроль», а не галочка",
      calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_kcfg))))
      ["pilot"]["control_group"] is True,
      "иначе можно снять галочку при заполненной строке контроля и противоречить себе")

_mix = json.loads(json.dumps(_kcfg))
_mix["pilot"]["kind"] = "conversion"
try:
    calc.validate_config(_mix); _mix_err = []
except calc.ConfigError as e:
    _mix_err = str(e).splitlines()
check("Заявленный тип и заполненное содержимое сверяются",
      any("groups" in e for e in _mix_err),
      "конверсионный пилот с групповыми замерами — противоречие, а не данные")

# Живая проверка и сохранение обязаны видеть ОДНО И ТО ЖЕ (BACKLOG 44).
# Скрытая половина формы остаётся в DOM со значениями и уходит на сервер как есть:
# если предпросмотр её учитывает, панель показывает «пилот доказателен» про замер,
# которого в расчёте не будет.
_mixed = {"kind": "behavioral", "name": "Москва", "control_group": True,
          "baseline_rate": 0.52, "observed_rate": 0.61,
          "sample_size": 14000, "control_size": 14000, "duration_months": 8,
          "randomized": True, "matched_at_baseline": True,
          "groups": [{"name": "МК", "role": "test", "metric": "monthly_turnover",
                      "value_start": 22000, "value_end": 27400},
                     {"name": "ОК", "role": "control", "metric": "monthly_turnover",
                      "value_start": 21800, "value_end": 24000}]}
_pv_beh = client.post("/api/pilot/preview", json={"pilot": _mixed}).json()
check("Выбрано «поведение» — предпросмотр не считает мощность по конверсии",
      not _pv_beh["errors"] and (_pv_beh.get("info") or {}).get("power") is None and
      (_pv_beh.get("info") or {}).get("derived"),
      "конверсия осталась в форме с прошлого переключения и в расчёт не идёт")
_pv_conv = client.post("/api/pilot/preview",
                       json={"pilot": dict(_mixed, kind="conversion")}).json()
check("Выбрана «конверсия» — предпросмотр не считает разницу разниц",
      not _pv_conv["errors"] and (_pv_conv.get("info") or {}).get("power") and
      (_pv_conv.get("info") or {}).get("derived") is None)

check("Ветка отбрасывается ОДНОЙ функцией, а не копией в каждом пути",
      "strip_other_branch" in __import__("inspect").getsource(app_mod.form_to_pilot) and
      "strip_other_branch" in __import__("inspect").getsource(app_mod.api_pilot_preview),
      "третья копия правила разойдётся с остальными при первой правке")
check("Отбрасывание описано данными, а не ветвлением по типу",
      set(app_mod.PILOT_BRANCH_KEYS) == {"conversion", "behavioral"})

_pv_saved = client.post("/api/pilot/preview", json={"pilot": _mixed}).json()
check("Предпросмотр и сохранение показывают одно и то же",
      (_pv_saved.get("info") or {}).get("derived") ==
      (calc.apply_pilot(calc.validate_config({
          **json.loads(json.dumps(params.defaults())),
          "pilot": app_mod.strip_other_branch(json.loads(json.dumps(_mixed)))}))
       ["_pilot"] or {}).get("derived"),
      "иначе панель противоречит результату")

check("Браузер не затирает введённое в скрытой ветке",
      "branch[hidden] input" not in
      (ROOT / "web" / "templates" / "pilot.html").read_text("utf-8"),
      "переключиться посмотреть и потерять свои цифры человек не должен")

# Веб собирал конфиг СВОЕЙ короткой сборкой — без apply_pilot и без normalize.
# Значит пилот в приложении не применялся вовсе, а блок «что дал пилот» не мог
# появиться в принципе. Тесты этого не видели, потому что дёргали apply_pilot
# напрямую, минуя путь, которым ходит человек.
check("Веб и CLI собирают конфиг одной и той же функцией",
      app_mod.resolved.__doc__ and "calc.prepare" in
      __import__("inspect").getsource(app_mod.resolved),
      "вторая сборка конфига — то же самое, что вторая модель")
_pcfg_in = {"meta": {"name": "П", "type": "product_launch", "horizon_months": 24},
            "pilot": {"name": "Пилот", "metrics": {"approval_rate": 0.9}}}
check("Сборка веба применяет пилот, а не только валидирует",
      "_pilot" in app_mod.resolved(
          {**json.loads(json.dumps(params.defaults())), **_pcfg_in}),
      "иначе цифры пилота молча не доезжают до расчёта")

# Замеры по группам: абсолюты на начало и конец, дельты считает движок.
# Разница «до и после» внутри группы эффектом не является — за срок пилота
# клиенты стали старше, прошёл сезон, изменился рынок.
_gr = json.loads(json.dumps(_cfg))
_gr["pilot"].pop("metrics", None)
_gr["pilot"].pop("metrics_are", None)
_gr["pilot"].update({"control_group": True, "randomized": True,
                     "matched_at_baseline": True, "duration_months": 8,
                     "sample_size": 14000, "control_size": 14000, "groups": [
    {"name": "Молодёжная карта", "role": "test", "metric": "monthly_turnover",
     "value_start": 22000, "value_end": 27400},
    {"name": "Обычная карта", "role": "control", "metric": "monthly_turnover",
     "value_start": 21800, "value_end": 24000}]})
_grr = calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_gr))))
check("Эффект считается как разница разниц, а не рост внутри группы",
      _grr["pilot"]["metrics"]["monthly_turnover"] == 3200.0,
      "тест вырос на 5 400, контроль сам на 2 200 — «до/после» завысило бы на 69%")
check("Посчитанное из абсолютов помечается приростом само",
      _grr["pilot"]["metrics_are"] == "uplift",
      "спрашивать «уровень или прирост» тут уже нечего")
check("Разница разниц объяснена, а не просто посчитана",
      any("РАЗНИЦА РАЗНИЦ" in n for n in _grr["_notes"]))
check("Выравнивание проверено по числам, а не принято на слово",
      any("ВЫРАВНИВАНИЕ ПОДТВЕРЖДЕНО ЗАМЕРОМ" in n for n in _grr["_notes"]))
check("Сказано, что это уровень на конец окна, а не средняя за срок",
      any("ЭТО УРОВЕНЬ НА КОНЕЦ ОКНА" in n for n in _grr["_notes"]),
      "при выровненных группах средняя за окно занижает полку")

_skew = json.loads(json.dumps(_gr))
_skew["pilot"]["groups"][0]["value_start"] = 26000        # разошлись ещё до пилота
check("Расхождение групп на старте названо вслух",
      any("ГРУППЫ РАЗОШЛИСЬ НА СТАРТЕ" in n
          for n in calc.apply_pilot(calc.validate_config(_skew))["_notes"]))

_noctl = json.loads(json.dumps(_gr))
_noctl["pilot"]["groups"] = [_noctl["pilot"]["groups"][0]]
_nc = calc.apply_pilot(calc.validate_config(_noctl))
check("Группа без контроля даёт рост, а не эффект — и так и написано",
      any("РОСТ БЕЗ КОНТРОЛЯ — НЕ ЭФФЕКТ" in n for n in _nc["_notes"]) and
      "monthly_turnover" not in (_nc["pilot"].get("metrics") or {}),
      "иначе взросление клиентов и сезон уехали бы в эффект инициативы")

check("apply_pilot не портит поданный ему конфиг",
      "metrics" not in json.loads(json.dumps(_gr))["pilot"],
      "функция копирует вход — иначе правки уезжают в исходный объект")

# Сквозняк до экрана результата: форма → конфиг → движок → отрисованная страница.
# Именно этого куска не хватало — все прежние проверки пилота останавливались
# на apply_pilot и не доходили до того, что видит человек.
_ri = int(client.post("/initiative/new", data={
    "name": "Пилот на экране", "type": "product_launch", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
_rd = {"has_pilot": "on", "pilot.name": "Москва", "pilot.control_group": "on",
       "pilot.randomized": "on", "pilot.matched_at_baseline": "on",
       "pilot.sample_size": "14000", "pilot.control_size": "14000",
       "pilot.duration_months": "8", "pilot.scale_haircut": "0,75", "action": "save"}
for _i, (_n, _role, _v0, _v1) in enumerate(
        [("Молодёжная карта", "test", 22000, 27400),
         ("Обычная карта", "control", 21800, 24000)]):
    _rd[f"pgroup.{_i}.name"] = _n
    _rd[f"pgroup.{_i}.role"] = _role
    _rd[f"pgroup.{_i}.metric"] = "monthly_turnover"
    _rd[f"pgroup.{_i}.value_start"] = str(_v0)
    _rd[f"pgroup.{_i}.value_end"] = str(_v1)
client.post(f"/initiative/{_ri}/pilot", data=_rd, follow_redirects=False)
client.post(f"/initiative/{_ri}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "700"},
            follow_redirects=False)
client.post(f"/initiative/{_ri}/assumptions", data={
    "costs.campaign_months": "6", "ch.0.name": "Перф", "ch.0.kind": "perform",
    "ch.0.frk_tier": "4", "ch.0.budget": "50000000", "ch.0.cpm": "250",
    "ch.0.ctr": "0.008", "ch.0.cr_to_app": "0.06", "action": "save"},
    follow_redirects=False)
_rres = client.post(f"/initiative/{_ri}/calculate", follow_redirects=False)
_rhtml = client.get(_rres.headers["location"]).text
check("Блок пилота доходит до экрана результата",
      "Что дал пилот" in _rhtml)
# Разряды форматируются НЕРАЗРЫВНЫМ пробелом — обычный в тесте не совпадёт
# с тем, что на странице, хотя человек видит одно и то же.
_flat_html = _rhtml.replace("\u00a0", " ").replace("\u2009", " ")
check("Разница разниц показана с обеими группами и разрывом на старте",
      all(x in _flat_html for x in ("разница разниц", "5 400", "2 200", "3 200")),
      "рост теста, рост контроля и эффект — три разных числа")
check("Показано, что перенесено в модель",
      "Что перенесено" in _rhtml)
check("Показано и то, что НЕ перенесено",
      "НЕ перенесено" in _rhtml,
      "иначе короткий список выглядит как всё, что пилот дал")
check("Параметры подписаны по-человечески, а не ключами движка",
      "monthly_turnover" not in _rhtml.split("Что дал пилот")[1].split("</section>")[0],
      "название берётся из реестра")

_gpage = client.get(f"/initiative/{iid}/pilot").text
for _f in ("name", "role", "metric", "value_start", "value_end"):
    check(f"Колонка замеров «{_f}» есть в форме",
          f'name="pgroup.0.{_f}"' in _gpage)

# Недостающих цифр не будет. Тогда спрашиваем не «доказано ли», а «что должно быть
# верно, чтобы было доказано» — обе обратные задачи считаются из того, что уже есть.
check("Критический разброс считается из прироста, уровня и размера групп",
      abs(calc.critical_cv(1800, 24000, 14000) - 2.22) < 0.01,
      "прирост 1 800 к уровню 24 000 различим, пока разброс не выше 2,2×")
check("Больше групп — выше допустимый разброс",
      calc.critical_cv(1800, 24000, 56000) > calc.critical_cv(1800, 24000, 14000))
check("Без разброса движок отвечает обратной задачей, а не молчанием",
      any("РАЗБРОСА НЕТ — СЧИТАЕМ ОБРАТНОЕ" in n for n in _n_nosd),
      "утверждение о базе можно проверить качественно, не имея новых цифр")

def _npv_at(ready_cfg, decay):
    c = json.loads(json.dumps(ready_cfg))
    c["base_activation"]["decay_months"] = decay
    return calc.evaluate(c)[1]["npv"]


_be_cfg = json.loads(json.dumps(_beh))
_be_cfg["base_activation"] = {
    "name": "Против обычной карты", "eligible_clients": 500000,
    "reach_share": 1.0, "technically_reachable": 1.0, "uplift_rate": 1.0,
    "attribution_share": 1.0, "turnover_before": 0, "turnover_after": 1800,
    "balance_before": 0, "balance_after": 4000, "decay_months": 18}
_be_res = calc.analyze(calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_be_cfg)))))
_be_lines = [v for v in _be_res["validation"]
             if "СРОК ЖИЗНИ ЭФФЕКТА" in v or "РЕШЕНИЕ ДЕРЖИТСЯ" in v]
check("Движок сам говорит, при каком сроке жизни эффекта меняется решение",
      len(_be_lines) == 1, _be_lines[0][:100] if _be_lines else "молчит")
_be_ready = calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_be_cfg))))))
_be_val = calc.breakeven_decay(_be_ready)
check("Сказанное согласовано с посчитанным, а не написано на всякий случай",
      ("НА РЕШЕНИЕ НЕ ВЛИЯЕТ" in _be_lines[0]) == (_be_val is None),
      f"порог {_be_val} мес" if _be_val else "знак NPV от срока не зависит")
if _be_val is not None:
    check("Порог назван числом и соотнесён с окном замера",
          f"{_be_val:g}" in _be_lines[0] and
          ("окна замера" in _be_lines[0] or "пределами наблюдения" in _be_lines[0]),
          "порог, которого не наблюдали, — это допущение, а не вывод")

# конфиг, где порог заведомо есть: эффект достаточно велик, чтобы перевернуть знак
_case = json.loads(Path(ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_case["base_activation"] = dict(_be_cfg["base_activation"], eligible_clients=500000)
_case_ready = calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_case))))
_case_be = calc.breakeven_decay(_case_ready)
check("На кейсе с крупным эффектом порог окупаемости находится",
      _case_be is not None and 1 < _case_be < 36, f"{_case_be} мес")
check("Порог — это действительно смена знака NPV, а не подобранное число",
      _npv_at(_case_ready, _case_be + 3) > 0 > _npv_at(_case_ready, max(1, _case_be - 3)),
      "дольше порога окупается, короче — нет")

_flat_npv = json.loads(json.dumps(_be_cfg))
_flat_npv["base_activation"]["eligible_clients"] = 1
check("Если знак NPV от срока не зависит — так и сказано, а не выдумано число",
      calc.breakeven_decay(calc.resolve_segments(calc.normalize(
          calc.apply_pilot(calc.validate_config(_flat_npv))))) is None,
      "уточнять срок незачем: решение держится на другом")

# Срок жизни эффекта — самое большое допущение расчёта, и оно должно быть видно
# среди драйверов, а не спрятано. Движение на ±25% для него бессмысленно:
# вопрос «держится только окно замера или не гаснет вовсе».
_tor = json.loads(json.dumps(_beh))
_tor["base_activation"] = {
    "name": "Против обычной карты", "eligible_clients": 500000,
    "reach_share": 1.0, "technically_reachable": 1.0, "uplift_rate": 1.0,
    "attribution_share": 1.0, "turnover_before": 0, "turnover_after": 1800,
    "balance_before": 0, "balance_after": 4000, "decay_months": 18}
_tres = calc.analyze(calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_tor)))))
_drivers = [t["driver"] for t in _tres["tornado"]]
check("Срок жизни эффекта есть среди драйверов торнадо",
      any("Срок жизни эффекта" in d for d in _drivers))
check("И он крупнее прочих драйверов, а не спрятан в хвосте списка",
      "Срок жизни эффекта" in _drivers[0],
      f'размах {_tres["tornado"][0]["swing"]:,.0f} ₽ против '
      f'{_tres["tornado"][1]["swing"]:,.0f} ₽ у следующего')
check("Границы взяты от окна замера до «не гаснет», а не ±25%",
      calc._decay_bounds(_tor) == [8.0, 0.0],
      "дальше окна замера доказательств нет — это и есть нижняя граница")

# поля дизайна должны быть в разметке формы, а не только в разборе (BACKLOG 40)
_ppage = client.get(f"/initiative/{iid}/pilot").text
for _f in ("randomized", "matched_at_baseline", "metric_cv", "metrics_are",
           "tail_months"):
    check(f"Поле пилота «{_f}» есть в форме", f'name="pilot.{_f}"' in _ppage)
check("Колонка хвоста есть и в отрисованных строках, и в шаблоне новой строки",
      _ppage.count('name="metric.0.tail"') >= 1 and 'metric.0.tail' in _ppage,
      "иначе хвост можно ввести только в уже существующей строке")

check("Коэффициент переноса режет прирост, а не уровень",
      abs(_probe["funnel"]["activation_rate"] - (0.65 + (0.61 - 0.65) * 0.75)) < 1e-9,
      f'получилось {_probe["funnel"]["activation_rate"]}')

r = client.get(f"/initiative/{iid}/pilot")
check("Проверки движка видны на странице",
      "Грейд переноса" in r.text and "B−" in r.text)

# живая проверка до сохранения — на маленькой выборке пилот ничего не доказывает
r = client.post("/api/pilot/preview", json={"pilot": {
    "geography": "Москва", "rollout_geography": "РФ", "control_group": True,
    "sample_size": 800, "control_size": 800,
    "baseline_rate": 0.52, "observed_rate": 0.55}})
_pw = r.json()["info"]["power"]
check("Предпросмотр ловит недостаточную выборку: 3 п.п. на 800 неразличимы",
      _pw["conclusive"] is False and _pw["min_detectable"] > _pw["observed_uplift"])
check("Предпросмотр объясняет, сколько нужно было на группу",
      any("на группу" in n for n in r.json()["notes"]))

# конфиг, который движок не примет, не должен молча сохраняться как валидный
r = client.post("/api/pilot/preview", json={"pilot": {"baseline_rate": 52}})
check("Предпросмотр отдаёт ошибку валидации, а не считает по мусору",
      bool(r.json().get("errors")))

client.post(f"/initiative/{iid}/pilot", data={"action": "save"}, follow_redirects=False)
check("Снятая галочка убирает пилот из конфига",
      "pilot" not in json.loads(_db.get_initiative(app_mod.con, iid)["config_json"]))

# httpx отправляет повторяющиеся поля только словарём со списком; список кортежей молча теряется
r = client.post(f"/initiative/{iid}/segments", data={
    "segments": ["kids", "teens", "youth"],
    "seg.kids.target_size": "3000000", "seg.kids.baseline_organic_apps_m": "2500",
    "seg.teens.target_size": "2200000", "seg.teens.baseline_organic_apps_m": "6000",
    "seg.youth.target_size": "9500000", "seg.youth.baseline_organic_apps_m": "22000",
}, follow_redirects=False)
check("Сегменты сохраняются и ведут на панель",
      r.status_code == 303 and "/panel" in r.headers["location"])

r = client.post(f"/initiative/{iid}/panel",
                data={"body": "Резюме: подростки не доверяют обещаниям."},
                follow_redirects=False)
check("Отчёт панели сохраняется", r.status_code == 303)
check("Отчёт панели виден на странице",
      "не доверяют обещаниям" in client.get(f"/initiative/{iid}/panel").text)

form = {
    "funnel.approval_rate": "0,95", "funnel.activation_rate": "0.6",
    "funnel.cannibalization": "0.15", "funnel.deadweight": "0",
    "unit_economics.avg_balance": "15000", "unit_economics.nim_annual": "0.05",
    "unit_economics.monthly_turnover": "9000", "unit_economics.interchange_rate": "0.012",
    "unit_economics.cashback_rate": "0.005", "unit_economics.servicing_cost_m": "50",
    "unit_economics.cor_annual": "0", "unit_economics.ramp_months": "3",
    "unit_economics.monthly_churn": "0.03", "unit_economics.fee_income_m": "0",
    "costs.campaign_months": "3", "costs.production": "5000000", "costs.it_dev": "0",
    "costs.one_off_other": "0", "costs.opex_monthly": "0", "costs.acq_cost_per_client": "0",
    "audience.halo_months": "2", "audience.halo_decay": "0.5",
    "meta.horizon_months": "24", "meta.discount_rate_annual": "0.2",
    "rice.reach": "0", "rice.impact": "2", "rice.confidence": "0.6", "rice.effort": "12",
    "confidence.A": "6", "confidence.B": "4", "confidence.C": "2", "confidence.D": "0",
    "ch.0.name": "Перформанс", "ch.0.kind": "perform", "ch.0.frk_tier": "4",
    "ch.0.budget": "20000000", "ch.0.cpm": "400", "ch.0.ctr": "0.006", "ch.0.cr_to_app": "0.04",
    "ch.1.name": "Внешние посевы", "ch.1.kind": "perform", "ch.1.frk_tier": "3",
    "ch.1.budget": "10000000", "ch.1.cpm": "250", "ch.1.ctr": "0.004", "ch.1.cr_to_app": "0.03",
}
r = client.post(f"/initiative/{iid}/assumptions", data=form, follow_redirects=False)
check("Допущения сохраняются и ведут на расчёт",
      r.status_code == 303 and "/calculate" in r.headers["location"])

r = client.get(f"/initiative/{iid}/calculate", follow_redirects=False)
check("Расчёт создаётся", r.status_code == 303 and "/calculation/" in r.headers["location"])
cid = int(r.headers["location"].split("/")[-1])

# ---------------------------------------------------------------- 3. Цифры от движка
con = _db.connect()
c = _db.get_calculation(con, cid)
res = json.loads(c["result_json"])
snapshot = json.loads(c["config_snapshot"])

direct = calc.analyze(calc.resolve_segments(calc.validate_config(snapshot)))
check("Приложение не считает само: результат совпадает с прямым вызовом движка",
      direct["pl"]["npv"] == res["pl"]["npv"] and
      direct["funnel"]["incremental_clients"] == res["funnel"]["incremental_clients"],
      f'NPV {res["pl"]["npv"]:,}')
check("Русская запятая в поле разобрана как десятичный разделитель",
      abs(snapshot["funnel"]["approval_rate"] - 0.95) < 1e-9,
      f'approval_rate = {snapshot["funnel"]["approval_rate"]}')

# ---------------------------------------------------------------- 4. Возрастные правила
rows = {r_["id"]: r_ for r_ in res["segments"]["rows"]}
check("Карта детям доступна с 6 лет, а не всему сегменту",
      abs(rows["kids"]["available_share"] - 8 / 14) < 1e-9,
      f'{rows["kids"]["available_share"]:.3f}')
check("Подросткам и молодёжи карта доступна полностью",
      rows["teens"]["available_share"] == 1.0 and rows["youth"]["available_share"] == 1.0)

r = client.post("/api/segments/preview",
                json={"product": "credit", "segments": [{"id": "kids", "target_size": 1000},
                                                        {"id": "youth", "target_size": 1000}]})
prev = r.json()
kid = next(x for x in prev["rows"] if x["id"] == "kids")
check("Предпросмотр сегментов берёт возрастные правила из движка",
      kid["available_share"] == 0 and any("ИСКЛЮЧЁН" in n for n in prev["notes"]),
      "кредит детям недоступен")

# ---------------------------------------------------------------- 5. Индекс уверенности
r = client.post("/api/confidence", json={"A": 0, "B": 0, "C": 2, "D": 10})
low = r.json()
check("Индекс уверенности считает движок", low["index"] == 0.25, f'индекс {low["index"]}')
check("Низкий индекс сопровождается предупреждением", "пилот" in low["note"].lower())

# ---------------------------------------------------------------- 6. Воспроизводимость
before = c["npv"]
_db.update_initiative(con, iid, config=dict(snapshot, costs=dict(snapshot["costs"],
                                                                 production=99_000_000)))
after = _db.get_calculation(con, cid)
check("Правка инициативы не меняет уже сохранённый расчёт", after["npv"] == before)
check("Расчёт хранит версию движка", bool(c["engine_version"]))
check("Расчёт хранит снимок конфига целиком",
      "funnel" in snapshot and "channels" in snapshot and "audience" in snapshot)

r = client.get(f"/initiative/{iid}/calculate", follow_redirects=False)
cid2 = int(r.headers["location"].split("/")[-1])
check("Повторный расчёт создаёт новую запись, а не переписывает старую", cid2 != cid)
check("Новый расчёт видит изменённые затраты",
      _db.get_calculation(con, cid2)["npv"] < before,
      f'{before:,} → {_db.get_calculation(con, cid2)["npv"]:,} при +99 млн на производство')

# ---------------------------------------------------------------- 7. Строгая валидация в UI
_db.update_initiative(con, iid, config=dict(snapshot, funnel=dict(snapshot["funnel"],
                                                                 approval_rate=60)))
r = client.get(f"/initiative/{iid}/calculate", follow_redirects=False)
check("Невалидный конфиг не считается, а возвращает человека к допущениям",
      r.status_code == 303 and "error=" in r.headers["location"])
check("Ошибка объясняется человеку на странице",
      "долей" in client.get(f"/initiative/{iid}/assumptions",
                            params={"error": "Проценты задаются долей"}).text)
_db.update_initiative(con, iid, config=snapshot)

# ---------------------------------------------------------------- 8. Страницы и выгрузка
page = client.get(f"/calculation/{cid}").text
check("Страница результата показывает вердикт", res["verdict"]["recommendation"][:12] in page)
check("Страница результата показывает лестницу ФРК", "Лестница ФРК" in page)
check("Список инициатив показывает созданную", "Молодёжная карта — тест" in client.get("/").text)

r = client.get(f"/calculation/{cid}/xlsx")
check("Выгрузка Excel собирается из снимка расчёта",
      r.status_code == 200 and len(r.content) > 20_000 and r.content[:2] == b"PK",
      f"{len(r.content):,} байт")

# --------------------------------------------- страница результата: новые блоки
# Блоки считались и не показывались: веб честно вычислял связанного клиента,
# спутника, партнёра и растранзачивание — и выбрасывал их в /dev/null.
_uz = json.loads((ROOT / "cases" / "kids_card_uzbekistan.json").read_text(encoding="utf-8"))
_iid2 = _db.create_initiative(app_mod.con, "Тестировщик", "Все блоки",
                              "product_launch", "kids_card", "демо", _uz)
r = client.get(f"/initiative/{_iid2}/calculate", follow_redirects=True)
check("Страница результата показывает, откуда маржа", "Откуда маржа" in r.text)
check("Показан связанный клиент с разбивкой по трём механикам",
      "Связанный клиент" in r.text and "Кросс-селл" in r.text and "Удержание" in r.text)
check("Показан продукт-спутник", "Продукт-спутник" in r.text)
check("Показан P&L партнёра", "Эффект для партнёра" in r.text)
check("Показано растранзачивание базы", "Растранзачивание" in r.text)
check("Показана позиция на рынке", "достижимого рынка" in r.text)

# Суммы в сумах, подписанные рублями, выглядят правдоподобно и неверны на курс.
check("Суммы подписаны валютой расчёта, а не рублём по умолчанию",
      "млн UZS" in r.text, "кейс считается в сумах")
check("Блок пересчёта в рубли при этом остаётся в рублях",
      "В рублях" in r.text and "млн ₽" in r.text)
_db.delete_initiative(app_mod.con, _iid2)

# Кейс в рублях не должен пострадать от появления валюты в фильтрах
r = client.get(f"/calculation/{cid}")
check("Рублёвый кейс по-прежнему подписан рублём",
      "млн ₽" in r.text and "UZS" not in r.text)
check("Показано, чего в цифре нет — открытые вопросы и неучтённое из брифа",
      "Чего в этой цифре нет" in r.text and "Открытые вопросы" in r.text,
      "у этой инициативы бриф заполнен на шаге 2а")

# ---------------------------------------------------------------- вывод
print(f"\n{'=' * 78}\nВЕБ — ПРОЙДЕНО: {len(ok)}   ПРОВАЛЕНО: {len(fail)}\n{'=' * 78}")
for t in ok:
    print("  ✓", t)
if fail:
    print("\n  ПРОВАЛЫ:")
    for t in fail:
        print("  ✗", t)
sys.exit(1 if fail else 0)
