#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка Excel-модели с движком.

Инвариант проекта: src/calc.py и docs/*.xlsx считают одно и то же.
Скрипт читает КЭШИРОВАННЫЕ значения Excel, поэтому файл должен быть пересчитан
(LibreOffice или открыть/сохранить в Excel) — иначе формулы читаются как None.

Запуск: make check
        python3 tests/check_xlsx_sync.py [кейс.json] [файл.xlsx]
"""
import json, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cases" / "frk1_debit_card.json"
XLSX = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "Калькулятор_эффекта_инициатив.xlsx"

spec = importlib.util.spec_from_file_location("calc", ROOT / "src" / "calc.py")
calc = importlib.util.module_from_spec(spec); spec.loader.exec_module(calc)

try:
    from openpyxl import load_workbook
except ImportError:
    sys.exit("Нужен openpyxl:  pip install -r requirements.txt")

if not XLSX.exists():
    sys.exit(f"Нет файла {XLSX}. Соберите: make xlsx")

wb = load_workbook(XLSX, data_only=True)
# tests/recalc.py возвращает книгу с заглавными именами листов — ищем без учёта регистра
name = next((s for s in wb.sheetnames if s.lower() == "модель"), None)
if name is None:
    sys.exit(f"В книге нет листа «Модель»: {wb.sheetnames}")
m = wb[name]

# Строки листа «Модель» ищем по подписи, а не по номеру: номера едут при любой правке модели.
LABELS = {"инкремент": "ИНКРЕМЕНТАЛЬНЫЕ КЛИЕНТЫ", "NPV": "NPV"}
ROW = {}
for key, label in LABELS.items():
    found = [c.row for c in m["A"] if isinstance(c.value, str) and c.value.strip() == label]
    if not found:
        sys.exit(f"На листе «Модель» не найдена строка «{label}» — сверять нечего.")
    ROW[key] = found[0]

TIER_COL = {4: "B", 3: "C", 2: "D", 1: "E"}
SCEN_COL = {"pessimistic": "F", "base": "G", "optimistic": "H"}
TOL = 1.0                                            # допуск в рублях/клиентах

cfg = calc.resolve_segments(json.loads(CASE.read_text(encoding="utf-8")))
rows = []

def cell(col, key):
    v = m[f"{col}{ROW[key]}"].value
    if v is None:
        sys.exit("Excel не пересчитан — формулы читаются как None.\n"
                 "Пересчитайте файл (LibreOffice/Excel) и повторите.")
    return v

def compare(title, col, npv, clients):
    x_npv, x_cl = cell(col, "NPV"), cell(col, "инкремент")
    d_npv, d_cl = abs(x_npv - npv), abs(x_cl - clients)
    rows.append((title, npv, x_npv, d_npv, clients, x_cl, d_cl, d_npv <= TOL and d_cl <= TOL))

ladder = calc.frk_ladder(cfg)
if ladder:
    for lvl in ladder["levels"]:
        compare(f"ФРК {lvl['frk_tier']}", TIER_COL[lvl["frk_tier"]],
                lvl["npv"], lvl["incremental_clients"])

for name, s in calc.SCEN.items():
    f_, p_ = calc.evaluate(calc.apply_scenario(cfg, s))
    compare(name, SCEN_COL[name], p_["npv"], f_["incremental_clients"])

print(f"\nСверка Excel ↔ движок  ({CASE.name})")
print(f"{'вариант':>14} {'NPV движок':>16} {'NPV Excel':>16} {'Δ':>8}   "
      f"{'клиенты движок':>15} {'клиенты Excel':>14} {'Δ':>6}")
for title, e_npv, x_npv, d_npv, e_cl, x_cl, d_cl, good in rows:
    print(f"{title:>14} {e_npv:>16,} {x_npv:>16,.0f} {d_npv:>8.2f}   "
          f"{e_cl:>15,} {x_cl:>14,.0f} {d_cl:>6.2f}  {'OK' if good else 'РАСХОЖДЕНИЕ'}")

bad = sum(1 for r in rows if not r[-1])
if bad:
    print(f"\n✗ Расхождений: {bad}. Excel и движок считают по-разному — см. BACKLOG.md, пункт P0.")
    sys.exit(1)
print("\n✓ Excel и движок сходятся.")
