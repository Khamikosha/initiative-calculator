#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Реестр параметров: разбивка по типам, подстановка из каталога, поля каналов."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

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
