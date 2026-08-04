#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка Excel-модели с движком.

Инвариант проекта: src/calc.py и docs/*.xlsx считают одно и то же.
Скрипт читает КЭШИРОВАННЫЕ значения Excel, поэтому файл должен быть пересчитан
(LibreOffice или открыть/сохранить в Excel) — иначе формулы читаются как None.

Запуск: make check
"""
import json, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "docs" / "Калькулятор_эффекта_инициатив.xlsx"
CASE = ROOT / "cases" / "frk1_debit_card.json"      # Excel собран под этот кейс

spec = importlib.util.spec_from_file_location("calc", ROOT / "src" / "calc.py")
calc = importlib.util.module_from_spec(spec); spec.loader.exec_module(calc)

try:
    from openpyxl import load_workbook
except ImportError:
    sys.exit("Нужен openpyxl:  pip install -r requirements.txt")

if not XLSX.exists():
    sys.exit(f"Нет файла {XLSX}. Соберите: make xlsx")

wb = load_workbook(XLSX, data_only=True)
m = wb["Модель"]

# строки листа «Модель» — синхронизированы с src/build_xlsx.py
ROW = {"инкремент": 26, "NPV": 39, "маркетинг": 43, "CPA": 45}
COL = {4: "B", 3: "C", 2: "D", 1: "E"}              # уровень ФРК -> колонка

cfg = json.loads(CASE.read_text(encoding="utf-8"))
ladder = calc.frk_ladder(cfg)
if not ladder:
    sys.exit("В кейсе нет frk_tier — нечего сверять.")

TOL = 1.0                                            # допуск в рублях/клиентах
rows, bad = [], 0
for lvl in ladder["levels"]:
    col = COL[lvl["frk_tier"]]
    x_npv = m[f"{col}{ROW['NPV']}"].value
    x_cl = m[f"{col}{ROW['инкремент']}"].value
    if x_npv is None:
        sys.exit("Excel не пересчитан — формулы читаются как None.\n"
                 "Пересчитайте файл (LibreOffice/Excel) и повторите.")
    d_npv, d_cl = abs(x_npv - lvl["npv"]), abs(x_cl - lvl["incremental_clients"])
    good = d_npv <= TOL and d_cl <= TOL
    bad += not good
    rows.append((lvl["frk_tier"], lvl["npv"], x_npv, d_npv,
                 lvl["incremental_clients"], x_cl, d_cl, good))

print(f"\nСверка Excel ↔ движок  ({CASE.name})")
print(f"{'ФРК':>4} {'NPV движок':>16} {'NPV Excel':>16} {'Δ':>8}   "
      f"{'клиенты движок':>15} {'клиенты Excel':>14} {'Δ':>6}")
for t, e_npv, x_npv, d_npv, e_cl, x_cl, d_cl, good in rows:
    print(f"{t:>4} {e_npv:>16,} {x_npv:>16,.0f} {d_npv:>8.2f}   "
          f"{e_cl:>15,} {x_cl:>14,.0f} {d_cl:>6.2f}  {'OK' if good else 'РАСХОЖДЕНИЕ'}")

if bad:
    print(f"\n✗ Расхождений: {bad}. Excel и движок считают по-разному — см. BACKLOG.md, пункт P0.")
    sys.exit(1)
print("\n✓ Excel и движок сходятся.")
