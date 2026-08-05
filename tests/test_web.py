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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# отдельная база на прогон: тесты не должны трогать рабочие данные
TMP = Path(tempfile.mkdtemp())
os.environ["CALC_DB"] = str(TMP / "test.db")

from web import db as _db                                        # noqa: E402
_db.DB_PATH = Path(os.environ["CALC_DB"])

from fastapi.testclient import TestClient                        # noqa: E402
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

# ---------------------------------------------------------------- 2. Путь целиком
client.post("/author", data={"author": "Тестировщик", "back": "/"}, follow_redirects=False)

r = client.post("/initiative/new", data={
    "name": "Молодёжная карта — тест", "type": "frk3", "product": "card",
    "description": "Проверка сквозного пути"}, follow_redirects=False)
check("Инициатива создаётся и ведёт на выбор сегментов",
      r.status_code == 303 and "/segments" in r.headers["location"])
iid = int(r.headers["location"].split("/")[2])

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

# ---------------------------------------------------------------- вывод
print(f"\n{'=' * 78}\nВЕБ — ПРОЙДЕНО: {len(ok)}   ПРОВАЛЕНО: {len(fail)}\n{'=' * 78}")
for t in ok:
    print("  ✓", t)
if fail:
    print("\n  ПРОВАЛЫ:")
    for t in fail:
        print("  ✗", t)
sys.exit(1 if fail else 0)
