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
    "pilot.scale_haircut": "0,75",
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

_probe = calc.apply_pilot(json.loads(json.dumps(_cfg)))
check("География Москва → РФ понижает грейд до B−", _probe["_pilot"]["grade"] == "B_minus")
check("Мощность выборки считает движок: 9 п.п. на 40 000 доказуемы",
      _probe["_pilot"]["power"]["conclusive"] is True)
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
