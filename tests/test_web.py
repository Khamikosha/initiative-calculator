#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тесты веб-пути: инициатива → бриф → пилот → сегменты → панель → допущения → расчёт.

Запуск: make test-web

Проверяем не «страница открылась», а свойства, ради которых приложение написано:
цифры приходят из движка, расчёт неизменяем, строгая валидация видна человеку,
возрастные правила применяются, реестр параметров не разошёлся с движком.

Сами проверки лежат по темам в web_*.py рядом. Здесь только порядок и итог.
Порядок ни на что не влияет: каждый файл заводит свои инициативы и ничего
не ждёт от соседей — раньше секции делили переменные, и вставка проверки
не в то место роняла прогон.
"""
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import webkit                                                   # noqa: E402

SUITES = ["web_registry", "web_steps", "web_effects", "web_contract",
          "web_assumptions", "web_walkthrough", "web_pilot"]

# Порядок наборов не должен влиять на результат: каждый заводит свои инициативы.
# Раньше это было не так — секции делили `iid` и `_cfg`, и вставка проверки не в то
# место роняла прогон. Чтобы связь не завелась снова, порядок ПЕРЕМЕШИВАЕТСЯ, если
# запуск помечен: `SHUFFLE=1 make test-web`. В CI гоняется и так, и так.
if os.environ.get("SHUFFLE"):
    random.Random(int(os.environ["SHUFFLE"])).shuffle(SUITES)
    print("порядок наборов перемешан:", ", ".join(SUITES))

for name in SUITES:
    __import__(name)

sys.exit(webkit.report())
