#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая обвязка веб-тестов: база, клиент, счётчик проверок.

Вынесено, потому что раньше все проверки жили одним файлом в 1800 строк, дописанным
хронологически. Это давало не только неудобство чтения: секции делили между собой
переменные (`iid`, `_cfg`), и вставка проверки не в то место роняла прогон
на NameError. Дважды за одну сессию.

Теперь каждый файл проверок самодостаточен: заводит свои инициативы и ничего
не ждёт от соседей. Порядок файлов в прогоне ни на что не влияет.
"""
import json
import os
import re
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

# Звёздочный импорт по умолчанию не тащит имена с подчёркиванием, а `_db` наборам нужен:
# перечисляем явно, чтобы каждый файл проверок начинался одной строкой, а не десятью.
__all__ = ["json", "os", "re", "sys", "Path", "unquote", "ROOT", "_db", "client",
           "ok", "fail", "check", "new_initiative", "walkthrough_initiative",
           "config_of", "result_of", "app_mod", "app", "calc", "params", "TestClient"]


def check(name, cond, detail=""):
    (ok if cond else fail).append(f"{name}" + (f" — {detail}" if detail else ""))


def new_initiative(name, type="product_launch", product="card", **extra):
    """Своя инициатива для каждого набора проверок — вместо общей на весь файл."""
    r = client.post("/initiative/new",
                    data={"name": name, "type": type, "product": product, **extra},
                    follow_redirects=False)
    return int(r.headers["location"].split("/")[2])


def walkthrough_initiative(name):
    """Инициатива с разобранным брифом — типовая заготовка для проверок дальше по пути.

    Раньше такую заводили один раз на весь файл, и всё, что шло ниже, от неё зависело.
    Стоило вставить проверку не в то место — прогон падал на NameError. Теперь каждый
    набор зовёт это сам и получает свою.
    """
    iid = new_initiative(name, type="frk3", product="card",
                         description="Проверка сквозного пути")
    client.post(f"/initiative/{iid}/brief", data={
        "claim.0.claim": "выдаём по всей стране",
        "claim.0.implication": "достижимый рынок — вся РФ",
        "claim.0.status": "applied", "claim.0.maps_to": "audience.reachable_market",
        "claim.0.grade": "C", "action": "save"}, follow_redirects=False)
    return iid


def config_of(iid):
    return json.loads(_db.get_initiative(_db.connect(), iid)["config_json"])


def result_of(location):
    return json.loads(_db.get_calculation(
        _db.connect(), int(location.split("/")[-1]))["result_json"])


def report():
    print(f"\n{'=' * 78}\nВЕБ — ПРОЙДЕНО: {len(ok)}   ПРОВАЛЕНО: {len(fail)}\n{'=' * 78}")
    for t in ok:
        print("  ✓", t)
    if fail:
        print("\n  ПРОВАЛЫ:")
        for t in fail:
            print("  ✗", t)
    return 1 if fail else 0
