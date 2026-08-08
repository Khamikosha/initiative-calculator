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

# Блоки движка, которых в книге пока нет: их формул build_xlsx.py не умеет.
# Кейс с любым из них сверять НЕЛЬЗЯ — но и молча пропускать нельзя: пропуск,
# который выглядит как успех, ровно так же врёт, как зелёная сверка не того с тем же.
# Поэтому отдельный исход: «не сверяется, и вот почему». См. BACKLOG 18.
BOOK_UNSUPPORTED = {
    "acquisition":     "план продаж с сезонностью и темпом год-к-году",
    "linked_client":   "связанный клиент (удержание, привлечение, кросс-селл)",
    "companion":       "продукт-спутник",
    "partner":         "P&L партнёра",
    "base_activation": "растранзачивание своей базы",
    "natural_effect":  "натуральный эффект и его перевод в деньги",
}
_raw = json.loads(CASE.read_text(encoding="utf-8"))
_missing = {k: v for k, v in BOOK_UNSUPPORTED.items() if _raw.get(k)}
if _missing:
    print(f"\n⊘ {CASE.name}: сверка с книгой не выполняется.")
    for k, v in _missing.items():
        print(f"    в кейсе есть «{k}» — {v}; в build_xlsx.py этого блока нет")
    print("  Инвариант «движок и книга считают одно и то же» на этом кейсе НЕ проверен.")
    print("  Достроить книгу или объявить её версией для базовых кейсов — BACKLOG 18.")
    sys.exit(0)

if not XLSX.exists():
    sys.exit(f"Нет файла {XLSX}. Соберите: make xlsx")

wb = load_workbook(XLSX, data_only=True)
# tests/recalc.py возвращает книгу с заглавными именами листов — ищем без учёта регистра
name = next((s for s in wb.sheetnames if s.lower() == "модель"), None)
if name is None:
    sys.exit(f"В книге нет листа «Модель»: {wb.sheetnames}")
m = wb[name]

# Строки листа «Модель» ищем по подписи, а не по номеру: номера едут при любой правке модели.
LABELS = {"инкремент": "ИНКРЕМЕНТАЛЬНЫЕ КЛИЕНТЫ", "NPV": "NPV",
          "АКБ": "АКБ — пик активной базы, чел", "ОД": "ОД за горизонт"}
ROW = {}
for key, label in LABELS.items():
    found = [c.row for c in m["A"] if isinstance(c.value, str) and c.value.strip() == label]
    if not found:
        sys.exit(f"На листе «Модель» не найдена строка «{label}» — сверять нечего.")
    ROW[key] = found[0]

TIER_COL = {4: "B", 3: "C", 2: "D", 1: "E"}
SCEN_COL = {"pessimistic": "F", "base": "G", "optimistic": "H"}
TOL = 1.0                                            # допуск в рублях/клиентах

# Через ту же точку входа, что движок и генератор книги. Обходить load_config здесь
# особенно опасно: проверяльщик, повторяющий ошибку проверяемого, всегда говорит «сходится».
cfg = calc.load_config(str(CASE))
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

# Эффект в разных валютах сверяется отдельно: NPV может сойтись, а АКБ и ОД разъехаться —
# они считаются по другой цепочке (пик активной базы и клиенто-месяцы).
_f, _p = calc.evaluate(cfg)
eff_rows = []
for title, key, want in (("АКБ, пик", "АКБ", _p["effect"]["akb_peak"]),
                         ("ОД за горизонт", "ОД", _p["effect"]["od_total"] or 0)):
    got = cell(SCEN_COL["base"], key)
    eff_rows.append((title, want, got, abs(got - want) <= max(1.0, abs(want) * 1e-6)))

# ---------------------------------------------------------------- флаги и вердикт
# Совпадения NPV мало: движок и книга могут сойтись в цифрах и разойтись в том, что
# по этим цифрам говорят человеку. Раньше так и было — двух флагов не хватало в движке.
# Тексты отличаются оформлением, поэтому сверяем по маркеру: сработал флаг или нет.
MARKERS = [
    "ВЕРДИКТ ПОНИЖЕН", "ИНВЕРСИЯ СЦЕНАРИЕВ", "контактов — сильный перебор",
    "Охват упёрся в потолок ЦА", "держится на brand-uplift", "LTV/CAC",
    "Окупаемости на заданном горизонте нет", "Выплаты идут за",
    "Cost of Risk = 0", "Каннибализация = 0",
]

it_name = next((s for s in wb.sheetnames if s.lower() == "итог"), None)
flag_rows = []
if it_name:
    it = wb[it_name]
    flag_rows = [str(c.value) for c in it["A"] if isinstance(c.value, str) and c.value.startswith("⚑")]

eng = calc.analyze(cfg)
eng_flags, xl_flags = eng["flags"], flag_rows
mismatch = []
for mk in MARKERS:
    in_eng = any(mk in f for f in eng_flags)
    in_xl = any(mk in f for f in xl_flags)
    if in_eng != in_xl:
        mismatch.append((mk, in_eng, in_xl))

# вердикт: сверяем не текст, а решение — понижен по качеству данных или нет
xl_verdict = str(wb[it_name]["B4"].value or "") if it_name else ""
v_eng = eng["verdict"]["downgraded_by_confidence"]
v_xl = xl_verdict.startswith("СНАЧАЛА ПИЛОТ")
verdict_ok = v_eng == v_xl

print(f"\nСверка Excel ↔ движок  ({CASE.name})")
print(f"{'вариант':>14} {'NPV движок':>16} {'NPV Excel':>16} {'Δ':>8}   "
      f"{'клиенты движок':>15} {'клиенты Excel':>14} {'Δ':>6}")
for title, e_npv, x_npv, d_npv, e_cl, x_cl, d_cl, good in rows:
    print(f"{title:>14} {e_npv:>16,} {x_npv:>16,.0f} {d_npv:>8.2f}   "
          f"{e_cl:>15,} {x_cl:>14,.0f} {d_cl:>6.2f}  {'OK' if good else 'РАСХОЖДЕНИЕ'}")

print(f"\nФлаги: движок {len(eng_flags)}, книга {len(xl_flags)} — "
      f"{'совпадают' if not mismatch else 'РАСХОЖДЕНИЕ'}")
for mk, in_eng, in_xl in mismatch:
    print(f"  ✗ «{mk}»: движок {'есть' if in_eng else 'нет'}, книга {'есть' if in_xl else 'нет'}")
print(f"Вердикт понижен по качеству данных: движок {v_eng}, книга {v_xl} — "
      f"{'совпадают' if verdict_ok else 'РАСХОЖДЕНИЕ'}")
for title, want, got, good in eff_rows:
    print(f"{title:>16}: движок {want:>16,.0f}  книга {got:>16,.0f}  "
          f"{'OK' if good else 'РАСХОЖДЕНИЕ'}")

bad = (sum(1 for r in rows if not r[-1]) + len(mismatch) + (0 if verdict_ok else 1)
       + sum(1 for r in eff_rows if not r[-1]))
if bad:
    print(f"\n✗ Расхождений: {bad}. Excel и движок считают или говорят по-разному — "
          "инвариант проекта нарушен.")
    sys.exit(1)
print("\n✓ Excel и движок сходятся — и в цифрах, и в предупреждениях.")
