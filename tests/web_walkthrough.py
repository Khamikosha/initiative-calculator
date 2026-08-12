#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сквозной путь и его результат: бриф, сегменты, панель, допущения, расчёт, выгрузка.

Путь и проверки результата держим вместе намеренно: вторые смотрят на расчёт,
который делает первый. Разнести их по файлам значило бы либо гонять путь дважды,
либо снова завести скрытую связь между файлами — ровно то, от чего уходим."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

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
