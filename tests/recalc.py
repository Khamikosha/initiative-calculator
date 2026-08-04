#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пересчёт формул xlsx без Excel и LibreOffice.

openpyxl читает только КЭШИРОВАННЫЕ значения, а свежесобранный файл их не содержит —
поэтому перед сверкой книгу надо пересчитать. Библиотека `formulas` вычисляет формулы
и сохраняет книгу со значениями. Имена листов при этом становятся заглавными —
tests/check_xlsx_sync.py ищет лист без учёта регистра.

Запуск: python3 tests/recalc.py вход.xlsx выход.xlsx
"""
import shutil, sys, tempfile
from pathlib import Path

try:
    import formulas
except ImportError:
    sys.exit("Нужен formulas:  pip install -r requirements.txt")

src, dst = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
dst.parent.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as tmp:
    xl = formulas.ExcelModel().loads(str(src)).finish()
    xl.calculate()
    xl.write(dirpath=tmp)
    produced = list(Path(tmp).glob("*.xlsx")) + list(Path(tmp).glob("*.XLSX"))
    if len(produced) != 1:
        sys.exit(f"Ожидали один пересчитанный файл, получили {len(produced)}.")
    shutil.copyfile(produced[0], dst)

print(f"Пересчитано: {dst}")
