#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка Excel-модели калькулятора эффекта инициатив (живые формулы).

Запуск: python3 src/build_xlsx.py [кейс.json] [выход.xlsx]
Вводные листа «Ввод» и строки листа «Каналы» заполняются из кейса — тот же JSON,
что ест движок. Это и делает сверку Excel ↔ движок осмысленной.
"""
import json, sys, importlib.util
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter as gcl, column_index_from_string as cix
from openpyxl.comments import Comment
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cases" / "frk1_debit_card.json"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "Калькулятор_эффекта_инициатив.xlsx"

_spec = importlib.util.spec_from_file_location("calc", Path(__file__).with_name("calc.py"))
calc = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(calc)

# Книга собирается из ТОГО ЖЕ конфига, что считает движок — через единственную точку входа.
# Раньше здесь стоял `resolve_segments(json.loads(...))`, то есть в обход validate_config,
# apply_pilot и normalize. Книга получала исходные значения кейса, движок — подставленные
# пилотом и производные: на кейсе с пилотом это 0,55 против 0,595 по активации и
# 24 000 против 31 500 по обороту. Сверка при этом была ЗЕЛЁНОЙ, потому что проверяльщик
# обходил load_config ровно так же и сравнивал две одинаково неверные картины.
CFG = calc.load_config(str(CASE))
SEG = CFG.get("_segments")

def q(section, key, default=0):
    v = CFG.get(section, {}).get(key, default)
    return default if v is None else v

F = "Arial"
BLUE = Font(name=F, size=10, color="0000FF")           # вводные
BLACK = Font(name=F, size=10)                           # формулы
GREEN = Font(name=F, size=10, color="008000")           # ссылки на другой лист
BOLD = Font(name=F, size=10, bold=True)
H1 = Font(name=F, size=14, bold=True, color="1F3864")
H2 = Font(name=F, size=11, bold=True, color="FFFFFF")
NOTE = Font(name=F, size=9, italic=True, color="666666")
FILL_H = PatternFill("solid", fgColor="1F3864")
FILL_KEY = PatternFill("solid", fgColor="FFFF00")
FILL_IN = PatternFill("solid", fgColor="EAF1FB")
FILL_RES = PatternFill("solid", fgColor="E8F3E8")
THIN = Border(*[Side(style="thin", color="BFBFBF")] * 4)

CUR = '#,##0 ₽;(#,##0 ₽);-'
CURM = '#,##0.0;(#,##0.0);-'
PCT = '0.0%'
PCT2 = '0.00%'
NUM = '#,##0;(#,##0);-'
NUM2 = '#,##0.00'

wb = Workbook()

# ==================================================================== ВВОД
ws = wb.active
ws.title = "Ввод"
ws.column_dimensions["A"].width = 42
ws.column_dimensions["B"].width = 16
ws.column_dimensions["C"].width = 10
ws.column_dimensions["D"].width = 20
ws.column_dimensions["E"].width = 74

ws["A1"] = "КАЛЬКУЛЯТОР ЭФФЕКТА ИНИЦИАТИВ — ВВОДНЫЕ"
ws["A1"].font = H1
ws["A2"] = ("Меняйте только СИНИЕ ячейки в колонке B. Чёрные и зелёные — формулы, их трогать не нужно. "
            "Жёлтая заливка = допущение, которое сильнее всего двигает результат: подтвердите его в первую очередь.")
ws["A2"].font = NOTE
ws.merge_cells("A2:E2")
ws["A3"] = "Проценты вводить долей: 0,15 = 15%. Грейд источника: A — свой факт из BI, B — свой аналог, C — рыночный бенчмарк, D — догадка."
ws["A3"].font = NOTE
ws.merge_cells("A3:E3")

hdr = ["Параметр", "Значение", "Ед.", "Источник / грейд", "Что это и как считается"]
for i, h in enumerate(hdr, 1):
    c = ws.cell(5, i, h); c.font = H2; c.fill = FILL_H; c.border = THIN

R = {}          # имя -> строка
r = 6

def section(title):
    global r
    c = ws.cell(r, 1, title); c.font = BOLD; c.fill = PatternFill("solid", fgColor="D9E2F3")
    for i in range(2, 6):
        ws.cell(r, i).fill = PatternFill("solid", fgColor="D9E2F3")
    r += 1

def inp(key, label, val, unit, src, note, fmt=NUM, key_assumption=False):
    global r
    ws.cell(r, 1, label).font = BLACK
    c = ws.cell(r, 2, val); c.font = BLUE; c.number_format = fmt; c.border = THIN
    c.fill = FILL_KEY if key_assumption else FILL_IN
    ws.cell(r, 3, unit).font = BLACK
    ws.cell(r, 4, src).font = BLACK
    ws.cell(r, 5, note).font = NOTE
    R[key] = r
    r += 1

section("МЕТА")
inp("name", "Название инициативы", q("meta", "name", ""), "", "—", "Свободный текст, попадает в лист «Итог».", "General")
inp("product", "Продукт", q("meta", "product", ""), "", "—",
    "От продукта зависят возрастные правила и то, обязателен ли Cost of Risk. "
    "Пусто — ни то, ни другое не проверяется.", "General")
inp("horizon", "Горизонт оценки", q("meta", "horizon_months", 24), "мес", "решение", "На сколько месяцев считаем эффект. Лист «Расчет_PL» рассчитан до 36 мес.")
inp("disc", "Ставка дисконтирования (годовая)", q("meta", "discount_rate_annual", 0.20), "%", "казначейство", "Стоимость капитала. Месячная ставка = (1+годовая)^(1/12)−1.", PCT)
inp("camp_m", "Длительность кампании", q("costs", "campaign_months", 1), "мес", "план", "За этот период тратится медиабюджет и приходят клиенты (равномерно).")

section("АУДИТОРИЯ")
inp("aud", "Размер целевой аудитории", q("audience", "target_size"), "чел", "медиапланер / C", "Знаменатель для охвата и частоты. Не вся РФ, а именно ЦА продукта.", NUM, True)
inp("base_apps", "Органические заявки в месяц (baseline)", q("audience", "baseline_organic_apps_m"), "шт/мес", "BI / A", "Текущий поток БЕЗ кампании. База, к которой применяется brand-uplift.", NUM, True)
inp("halo_m", "Halo-период после кампании", q("audience", "halo_months", 2), "мес", "бенчмарк / C", "Сколько месяцев brand-эффект тянется после открутки.")
inp("halo_d", "Затухание halo", q("audience", "halo_decay", 0.5), "доля", "бенчмарк / C", "Какая доля эффекта сохраняется в halo-период.", PCT)

section("ВОРОНКА")
inp("approval", "Approval rate", q("funnel", "approval_rate", 1.0), "%", "риски / A", "Доля одобренных заявок. Карты 50–70%, кредиты 20–40%, вклады ~95%.", PCT, True)
inp("activation", "Activation rate", q("funnel", "activation_rate", 1.0), "%", "BI / A", "Доля одобренных, дошедших до целевого действия. Самая недооценённая дыра воронки.", PCT, True)
inp("cannib", "Каннибализация", q("funnel", "cannibalization"), "%", "оценка / C", "Доля клиентов, переехавших со своих же продуктов. Ноль ставить только с обоснованием.", PCT, True)
inp("deadweight", "Deadweight (для акций)", q("funnel", "deadweight"), "%", "оценка / D", "Доля тех, кто купил бы и без промо. Для акций 30–60%, для кампаний обычно 0.", PCT)

section("ЮНИТ-ЭКОНОМИКА КЛИЕНТА")
inp("balance", "Средний баланс", q("unit_economics", "avg_balance"), "₽", "BI / A", "Остаток на счёте. Источник процентной маржи.", CUR, True)
inp("nim", "NIM / FTP-спред (годовой)", q("unit_economics", "nim_annual"), "%", "казначейство / A", "Ставка, по которой банк зарабатывает на остатке.", PCT)
inp("turnover", "Оборот по карте в месяц", q("unit_economics", "monthly_turnover"), "₽", "BI / A", "База для интерчейнджа и кэшбэка.", CUR)
inp("interchange", "Ставка интерчейнджа", q("unit_economics", "interchange_rate"), "%", "эквайринг / A", "Комиссия от оборота, обычно 0,8–1,4%.", PCT2)
inp("fee", "Прочий комиссионный доход", q("unit_economics", "fee_income_m"), "₽/мес", "продукт", "Подписка, СМС-информирование, страховки и т.п.", CUR)
inp("cashback", "Кэшбэк (доля оборота)", q("unit_economics", "cashback_rate"), "%", "продукт / A", "Программа лояльности. Прямой вычет из маржи.", PCT2)
inp("servicing", "Стоимость обслуживания", q("unit_economics", "servicing_cost_m"), "₽/мес", "финансы / B", "Себестоимость поддержки клиента: процессинг, саппорт, выпуск.", CUR)
inp("cor", "Cost of Risk (годовой)", q("unit_economics", "cor_annual"), "%", "риски / A", "ОБЯЗАТЕЛЬНО для кредитных продуктов. Для дебетовых 0.", PCT, True)
inp("ramp", "Ramp-up маржи", q("unit_economics", "ramp_months", 1), "мес", "BI / B", "За сколько месяцев клиент выходит на полную маржу. Маржа множится на МИН(1; месяц/ramp).")
inp("churn", "Месячный отток", q("unit_economics", "monthly_churn"), "%", "BI / A", "Доля активных, отваливающихся за месяц. Определяет LTV.", PCT, True)
inp("od", "ОД на клиента в год", q("unit_economics", "operating_income_annual"), "₽/год",
    "отчётность / A",
    "Операционный доход на клиента за год. Это ДОХОД, а не маржа: кэшбэк, обслуживание "
    "и риск он не вычитает. Нужен для строки «эффект в ОД» на листе «Итог».", CUR)
inp("mode", "Режим экономики клиента", q("unit_economics", "mode", "build_up"), "", "решение",
    "build_up — собрать снизу вверх из строк выше. value_per_client — взять готовую "
    "дисконтированную ценность клиента одним числом (строка ниже); тогда строки выше "
    "в расчёте НЕ участвуют, а срок окупаемости не считается.", "General")
inp("value_pc", "Ценность клиента (готовая)", q("unit_economics", "value_per_client"), "₽",
    "модель продукта / A",
    "Дисконтированная ценность одного клиента за свой горизонт, БЕЗ затрат на привлечение. "
    "Отток, ramp-up и обслуживание уже внутри неё — поэтому поверх они не применяются.", CUR, True)
inp("value_h", "Горизонт ценности", q("unit_economics", "value_horizon_months", 36), "мес", "модель продукта",
    "За сколько месяцев посчитана ценность. Обязан совпадать с горизонтом оценки, иначе "
    "затраты и доходы считаются на разные сроки.")

section("ЗАТРАТЫ")
inp("production", "Производство и креатив", q("costs", "production"), "₽", "смета", "Разовые, списываются в 1-й месяц. Входят в маркетинговые затраты для ROMI.", CUR)
inp("itdev", "IT-разработка", q("costs", "it_dev"), "₽", "смета", "Только для запуска продукта на полку. В ROMI НЕ входит, в NPV — да.", CUR)
inp("other", "Прочие разовые затраты", q("costs", "one_off_other"), "₽", "смета", "Юристы, комплаенс, лицензии, интеграции.", CUR)
inp("opex", "Постоянные расходы", q("costs", "opex_monthly"), "₽/мес", "смета", "Run-rate: поддержка продукта, команда. Списывается каждый месяц горизонта.", CUR)
inp("acq", "Выплата за привлечение, ₽/клиента", q("costs", "acq_cost_per_client"), "₽", "программа",
    "Реферальные выплаты, промокоды, бонус за открытие. Платится за КАЖДОГО сработавшего "
    "(activated), не за инкремент — см. строку «Выплаты за привлечение» на листе «Модель».", CUR)

section("RICE (для сравнения инициатив между собой)")
inp("impact", "Impact", q("rice", "impact", 1), "0.25–3", "экспертно", "Насколько сильно влияет на одного затронутого клиента. 3 — массивно, 0.25 — минимально.")
inp("conf", "Confidence", q("rice", "confidence", 0.5), "%", "экспертно", "Уверенность в оценке. Синхронизируйте с индексом качества данных.", PCT)
inp("effort", "Effort", q("rice", "effort", 1), "чел-мес", "команда", "Трудозатраты всех команд. Знаменатель RICE.")
inp("hurdle", "Норма LTV/CAC", q("rice", "hurdle_ltv_cac", 3.0), "раз", "решение",
    "От неё считается предельная цена привлечения: платить за клиента можно не больше, "
    "чем его ценность, делённая на эту норму. Раньше в книге она была зашита тройкой.", NUM2)

section("КАЧЕСТВО ДАННЫХ — грейд источника каждого допущения")
inp("gA", "Допущений грейда A — свой факт из BI", q("confidence", "A"), "шт", "—",
    "Вес 1,00. Прошлые кампании этого же продукта, выгрузка из DWH.")
inp("gB", "Допущений грейда B — свой аналог", q("confidence", "B"), "шт", "—",
    "Вес 0,75. Другой продукт, другая ЦА или другой период, но свои данные.")
inp("gBm", "Допущений грейда B− — перенос с другого рынка", q("confidence", "B_minus"), "шт", "—",
    "Вес 0,60. Свой факт, но с чужого рынка или из другой географии: цифра настоящая, "
    "перенос — допущение.")
inp("gC", "Допущений грейда C — рыночный бенчмарк", q("confidence", "C"), "шт", "—",
    "Вес 0,50. Публичная отчётность, отраслевое исследование.")
inp("gD", "Допущений грейда D — догадка", q("confidence", "D"), "шт", "—", "Вес 0,20.")


def frm(key, label, formula, unit, note, fmt=NUM2):
    """Строка-формула на листе «Ввод»: считается, а не вводится."""
    global r
    ws.cell(r, 1, label).font = BOLD
    c = ws.cell(r, 2, formula); c.font = BLACK; c.number_format = fmt
    c.border = THIN; c.fill = FILL_RES
    ws.cell(r, 3, unit).font = BLACK
    ws.cell(r, 4, "формула").font = BLACK
    ws.cell(r, 5, note).font = NOTE
    R[key] = r
    r += 1


# Веса и состав грейдов берутся ИЗ ДВИЖКА, а не переписываются руками: пока список
# был зашит константой, грейд B− существовал в calc.py и отсутствовал в книге — пять
# допущений просто выпадали из знаменателя, и индекс расходился (0,632 против 0,640).
_GRADE_CELL = {"A": "gA", "B": "gB", "B_minus": "gBm", "C": "gC", "D": "gD"}
_GT = "(" + "+".join(f'$B${R[_GRADE_CELL[g]]}' for g in calc.GRADE_W) + ")"
_NUM = "+".join(f'$B${R[_GRADE_CELL[g]]}*{w}' for g, w in calc.GRADE_W.items())
frm("conf_idx", "ИНДЕКС УВЕРЕННОСТИ",
    f'=IF({_GT}=0,"",ROUND(({_NUM})/{_GT},2))',
    "0–1",
    "Взвешенное среднее по грейдам. Ниже 0,6 вердикт на листе «Итог» понижается до «сначала пилот» "
    "при любом NPV: модель, где половина допущений из воздуха, даёт разброс шире собственного ответа.")

ws.freeze_panes = "A6"
V = {k: f"Ввод!$B${v}" for k, v in R.items()}
# движок берёт max(1, …) — повторяем, иначе ноль в этих ячейках даёт #ДЕЛ/0! вместо числа
CAMP = f'MAX(1,{V["camp_m"]})'
RAMP = f'MAX(1,{V["ramp"]})'

# =============================================================== СЕГМЕНТЫ
# Лист появляется, только если в кейсе выбраны возрастные сегменты. Тогда соответствующие
# ячейки «Ввода» перестают быть вводными и становятся ссылками на взвешенный итог.
if SEG:
    sg = wb.create_sheet("Сегменты")
    sg["A1"] = "ВОЗРАСТНЫЕ СЕГМЕНТЫ"
    sg["A1"].font = H1
    sg["A2"] = ("Считаем ОДИН прогон по взвешенным допущениям, а не N прогонов. Веса: воронка — по "
                "эффективной аудитории, экономика клиента — по числу клиентов, а ставки (NIM, интерчейндж, "
                "кэшбэк, CoR) — по своей базе (баланс или оборот). Из-за этого баланс×NIM и оборот×интерчейндж "
                "совпадают с посегментной суммой точно, а не приблизительно.")
    sg["A2"].font = NOTE
    sg.merge_cells("A2:AC2")
    sg["A3"] = ("Возрастные ограничения — правила банка: счёт и вклад до 14 лет открывает третье лицо в офисе "
                "по свидетельству о рождении; с 6 лет — детская карта и копилки в детском приложении; с 14 — "
                "вход в приложение, накопительные продукты, брокерский счёт, подписка и лояльность; с 18 — всё.")
    sg["A3"].font = NOTE
    sg.merge_cells("A3:AE3")

    SEG_HDR = [("Сегмент", 22), ("Ключ", 9), ("Включён", 10), ("Размер сегмента, чел", 16),
               ("Продукт доступен с, лет", 14), ("Доля доступных возрастов", 14),
               ("Достижимая доля", 13), ("Эффективная ЦА, чел", 16), ("Органика, заявок/мес", 14),
               ("Approval", 10), ("Activation", 10), ("Каннибализация", 13), ("Deadweight", 11),
               ("Средний баланс", 14), ("NIM", 9), ("Оборот/мес", 12), ("Интерчейндж", 11),
               ("Комиссии/мес", 12), ("Кэшбэк", 9), ("Обслуживание/мес", 13), ("CoR", 9),
               ("Ramp-up, мес", 11), ("Отток/мес", 10), ("Выплата за привлечение", 14),
               ("вес: одобренные", 13), ("вес: клиенты", 12), ("база баланса", 13),
               ("база оборота", 13), ("Доля в клиентах", 12), ("Ценность клиента, ₽", 15), ("ОД на клиента/год, ₽", 15)]
    for i, (h, w) in enumerate(SEG_HDR, 1):
        c = sg.cell(5, i, h); c.font = H2; c.fill = FILL_H; c.border = THIN
        c.alignment = Alignment(wrap_text=True, vertical="center")
        sg.column_dimensions[gcl(i)].width = w
    sg.row_dimensions[5].height = 42

    SEG_FMT = {4: NUM, 5: NUM, 6: PCT, 7: PCT, 8: NUM, 9: NUM, 10: PCT, 11: PCT, 12: PCT, 13: PCT,
               14: CUR, 15: PCT, 16: CUR, 17: PCT2, 18: CUR, 19: PCT2, 20: CUR, 21: PCT,
               22: NUM2, 23: PCT, 24: CUR, 25: NUM, 26: NUM, 27: NUM, 28: NUM, 29: PCT, 30: CUR, 31: CUR}
    SEG_KEYS = {4: "target_size", 6: "available_share", 7: "reachable_share", 9: "organic_apps_m",
                10: "approval_rate", 11: "activation_rate", 12: "cannibalization", 13: "deadweight",
                14: "avg_balance", 15: "nim_annual", 16: "monthly_turnover", 17: "interchange_rate",
                18: "fee_income_m", 19: "cashback_rate", 20: "servicing_cost_m", 21: "cor_annual",
                22: "ramp_months", 23: "monthly_churn", 24: "acq_cost_per_client",
                30: "value_per_client", 31: "operating_income_annual"}
    SEG_FIRST = 6
    for k, srow in enumerate(SEG["rows"]):
        i = SEG_FIRST + k
        sg.cell(i, 1, srow["name"]).font = BLACK
        sg.cell(i, 2, srow["id"]).font = BLACK
        sg.cell(i, 3, "ДА" if srow["available_share"] > 0 else "НЕТ").font = BLUE
        sg.cell(i, 5, srow["min_age"]).font = BLACK
        for col, key in SEG_KEYS.items():
            c = sg.cell(i, col, srow[key]); c.font = BLACK if col in (5, 6) else BLUE
            c.fill = FILL_IN
        sg.cell(i, 8, f'=IF($C{i}="ДА",$D{i}*$G{i},0)').font = BLACK
        sg.cell(i, 25, f'=$H{i}*$J{i}').font = BLACK
        sg.cell(i, 26, f'=$Y{i}*$K{i}').font = BLACK
        sg.cell(i, 27, f'=$Z{i}*$N{i}').font = BLACK
        sg.cell(i, 28, f'=$Z{i}*$P{i}').font = BLACK
        sg.cell(i, 29, f'=IF(SUM($Z${SEG_FIRST}:$Z${SEG_FIRST + len(SEG["rows"]) - 1})=0,0,'
                       f'$Z{i}/SUM($Z${SEG_FIRST}:$Z${SEG_FIRST + len(SEG["rows"]) - 1}))').font = BLACK
        for col, fmt in SEG_FMT.items():
            sg.cell(i, col).number_format = fmt
            sg.cell(i, col).border = THIN
        for col in (25, 26, 27, 28, 29):
            sg.cell(i, col).fill = PatternFill("solid", fgColor="F2F2F2")

    SEG_LAST = SEG_FIRST + len(SEG["rows"]) - 1
    TOT = SEG_LAST + 1

    def _rng(col):
        return f"${col}${SEG_FIRST}:${col}${SEG_LAST}"

    def _weighted(base_col, val_col):
        """Взвешенное среднее val по весам base — с защитой от нулевого знаменателя."""
        b, v = _rng(base_col), _rng(val_col)
        return f'=IF(SUM({b})=0,0,SUMPRODUCT({b},{v})/SUM({b}))'

    sg.cell(TOT, 1, "ИТОГО (взвешенное)").font = BOLD
    sg.cell(TOT, 4, f"=SUM({_rng('D')})")
    sg.cell(TOT, 8, f"=SUM({_rng('H')})")
    sg.cell(TOT, 9, f"=SUM({_rng('I')})")
    for col, base in (("J", "H"), ("K", "Y"), ("L", "Z"), ("M", "Z"), ("N", "Z"), ("O", "AA"),
                      ("P", "Z"), ("Q", "AB"), ("R", "Z"), ("S", "AB"), ("T", "Z"), ("U", "AA"),
                      ("V", "Z"), ("W", "Z"), ("X", "Z"), ("AD", "Z"), ("AE", "Z")):
        sg.cell(TOT, cix(col), _weighted(base, col))
    for col in list(SEG_FMT) + [1]:
        c = sg.cell(TOT, col)
        c.font = BOLD if col == 1 else Font(name=F, size=10, bold=True)
        c.number_format = SEG_FMT.get(col, "General")
        c.fill = FILL_RES
        c.border = THIN
    sg.freeze_panes = "C6"

    # ячейки «Ввода», которыми теперь управляет лист «Сегменты»
    for key, col in (("aud", "H"), ("base_apps", "I"), ("approval", "J"), ("activation", "K"),
                     ("cannib", "L"), ("deadweight", "M"), ("balance", "N"), ("nim", "O"),
                     ("turnover", "P"), ("interchange", "Q"), ("fee", "R"), ("cashback", "S"),
                     ("servicing", "T"), ("cor", "U"), ("ramp", "V"), ("churn", "W"), ("acq", "X"),
                     ("value_pc", "AD"), ("od", "AE")):
        c = ws.cell(R[key], 2, f"=Сегменты!${col}${TOT}")
        c.font = GREEN; c.fill = FILL_RES
        ws.cell(R[key], 4, "лист «Сегменты»").font = BLACK

# ================================================================= КАНАЛЫ
ch = wb.create_sheet("Каналы")
ch["A1"] = "МЕДИАПЛАН ПО КАНАЛАМ"
ch["A1"].font = H1
ch["A2"] = ("Синие колонки A–I заполняете вы, серые J–Q считаются сами. Пустые строки игнорируются. "
            "Тип: internal — свои каналы (push/SMS), perform — прямой отклик, brand — охватные (ТВ, наружка).")
ch["A2"].font = NOTE
ch.merge_cells("A2:Q2")
ch["A3"] = ("ФРК-уровень: внутренние и перформанс = 4, внешние посевы = 3, наружная = 2, ТВ = 1. "
            "Уровни вложены: ФРК 4 ⊂ 3 ⊂ 2 ⊂ 1, поэтому лист «Модель» сам построит лестницу.")
ch["A3"].font = NOTE
ch.merge_cells("A3:Q3")

ch_hdr = [("Канал", 24), ("Тип", 11), ("ФРК-уровень", 12), ("Бюджет, ₽", 15), ("CPM, ₽", 10),
          ("CTR / response", 13), ("CR клик→заявка", 14), ("Brand uplift", 12), ("Охват абс. (internal)", 18),
          ("Показы", 14), ("Охват, % ЦА", 12), ("Частота", 9), ("Заявки прямые", 13),
          ("Вклад в brand-uplift", 17), ("Стоимость заявки, ₽", 17), ("Ключ уровня", 11), ("LN(1−охват)", 12), ("Предупреждение", 52)]
for i, (h, w) in enumerate(ch_hdr, 1):
    c = ch.cell(5, i, h); c.font = H2; c.fill = FILL_H; c.border = THIN
    c.alignment = Alignment(wrap_text=True, vertical="center")
    ch.column_dimensions[gcl(i)].width = w
ch.row_dimensions[5].height = 30

def _ch_row(c):
    kind = c.get("kind", "perform")
    # у канала без frk_tier уровень 4: он попадает в любую маску, как и в движке,
    # где лестница просто не строится при единственном уровне
    return (c.get("name", kind), kind, int(c.get("frk_tier", 4) or 4), c.get("budget"), c.get("cpm"),
            c.get("response_rate") if kind == "internal" else c.get("ctr"),
            c.get("cr_to_app"), c.get("brand_uplift"), c.get("reach_abs"))

channels = [_ch_row(c) for c in CFG.get("channels", [])]
FIRST = 6
LAST = FIRST + max(10, len(channels)) - 1          # минимум 10 пустых строк под каналы
for i in range(FIRST, LAST + 1):
    src = channels[i - FIRST] if i - FIRST < len(channels) else (None,) * 9
    for j, val in enumerate(src, 1):
        c = ch.cell(i, j, val); c.font = BLUE; c.border = THIN; c.fill = FILL_IN
    for j, fmt in ((4, CUR), (5, CUR), (6, PCT2), (7, PCT), (8, PCT), (9, NUM)):
        ch.cell(i, j).number_format = fmt
    A, B_, D, E, Ff, G, H, I, K, L, P = (f"$A{i}", f"$B{i}", f"$D{i}", f"$E{i}", f"$F{i}",
                                          f"$G{i}", f"$H{i}", f"$I{i}", f"$K{i}", f"$L{i}", f"$P{i}")
    fml = {
        10: f'=IF({A}="",0,IF({B_}="internal",N({I}),IF(N({E})>0,{D}/{E}*1000,0)))',
        11: f'=IF({A}="",0,IF({B_}="internal",MIN(J{i}/{V["aud"]},0.98),MIN(1-EXP(-J{i}/{V["aud"]}),0.98)))',
        12: f'=IF(K{i}>0,(J{i}/{V["aud"]})/K{i},0)',
        13: f'=IF(OR({A}="",{B_}="brand"),0,J{i}*N({Ff})*N({G}))',
        14: f'=IF(AND({A}<>"",{B_}="brand"),N({H})*K{i}*MIN(1,L{i}/3),0)',
        15: f'=IF(M{i}>0,{D}/M{i},"")',
        16: f'=IF(OR({A}="",{ch.cell(i,3).coordinate}=""),0,C{i})',
        17: f'=LN(1-MIN(K{i},0.98))',
        18: (f'=IF({A}="","",IF(AND({D}>0,M{i}=0,N{i}=0),'
             f'"БЮДЖЕТ ВПУСТУЮ: канал тратит деньги и не даёт ни заявок, ни brand-эффекта — '
             f'обычно это неверный тип канала или незаполненные конверсии",'
             f'IF(AND({B_}="brand",K{i}>=0.97,L{i}>=4.5),'
             f'"НАСЫЩЕНИЕ: охват и частота на потолке — дополнительный бюджет сюда НЕ даёт заявок вообще",'
             f'IF(AND({B_}="perform",K{i}>=0.97,L{i}>=8),'
             f'"Частота высокая при полном охвате — модель линейна, занизьте CTR на 15–30% (выгорание)",""))))'),
    }
    for col, f_ in fml.items():
        c = ch.cell(i, col, f_); c.border = THIN
        c.font = Font(name=F, size=10, color="C00000") if col == 18 else BLACK
        c.fill = PatternFill("solid", fgColor="F2F2F2")
    for col, fmt in ((10, NUM), (11, PCT), (12, NUM2), (13, NUM), (14, PCT2), (15, CUR), (16, NUM), (17, NUM2), (18, "General")):
        ch.cell(i, col).number_format = fmt

ch.cell(5, 10).comment = Comment("Показы = бюджет / CPM × 1000. Для internal — заданный охват базы.", "Модель")
ch.cell(5, 11).comment = Comment("Кривая охвата Пуассона: Reach(1+) = 1 − e^(−λ), где λ = показы / размер ЦА.\n"
                                 "Потолок 98%: 100% аудитории не охватывается никогда.", "Модель")
ch.cell(5, 12).comment = Comment("Частота среди охваченных = λ / охват. Эффективная частота 3–6; выше 10 — перерасход.", "Модель")
ch.cell(5, 14).comment = Comment("Для brand-каналов: uplift × охват × МИН(1; частота/3).\n"
                                 "Полный эффект достигается только при частоте 3+.", "Модель")
ch.cell(5, 16).comment = Comment("Служебная: 0 для пустых строк, иначе ФРК-уровень. Нужна, чтобы пустые строки не ломали лестницу.", "Модель")
ch.cell(5, 17).comment = Comment("Служебная: охваты каналов складываются как 1 − Π(1 − охват_i), через логарифмы.", "Модель")
ch.freeze_panes = "A6"

CP = f"Каналы!$P${FIRST}:$P${LAST}"
CJ = f"Каналы!$J${FIRST}:$J${LAST}"
CM = f"Каналы!$M${FIRST}:$M${LAST}"
CN = f"Каналы!$N${FIRST}:$N${LAST}"
CQ = f"Каналы!$Q${FIRST}:$Q${LAST}"
CD = f"Каналы!$D${FIRST}:$D${LAST}"

# ================================================================= МОДЕЛЬ
m = wb.create_sheet("Модель")
m["A1"] = "МОДЕЛЬ — 7 вариантов расчёта"
m["A1"].font = H1
m["A2"] = ("Слева направо: четыре уровня ФРК (вложенные наборы каналов) и три сценария на полном плане. "
           "Все колонки считаются одной и той же формулой — отличаются только рычаги в строках 5–9.")
m["A2"].font = NOTE
m.merge_cells("A2:H2")
m.column_dimensions["A"].width = 44
for i in range(2, 9):
    m.column_dimensions[gcl(i)].width = 15
m.column_dimensions["J"].width = 80

VAR = ["ФРК 4", "ФРК 3", "ФРК 2", "ФРК 1", "Пессимистичный", "Базовый", "Оптимистичный"]
for i, v in enumerate(VAR, 2):
    c = m.cell(4, i, v); c.font = H2; c.fill = FILL_H; c.border = THIN
    c.alignment = Alignment(horizontal="center", wrap_text=True)
m.cell(4, 1, "Показатель").font = H2
m.cell(4, 1).fill = FILL_H

LEVERS = [
    ("Маска ФРК (берём каналы с уровнем ≥)", [4, 3, 2, 1, 1, 1, 1], NUM,
     "Фильтр каналов. ФРК 4 = только уровень 4; ФРК 1 = все каналы."),
    ("Множитель конверсий", [1, 1, 1, 1, 0.6, 1.0, 1.3], PCT,
     "Применяется к CTR, response rate, CR и brand-uplift одновременно."),
    ("Множитель маржи", [1, 1, 1, 1, 0.75, 1.0, 1.15], PCT,
     "Применяется только к ДОХОДНОЙ части маржи. Расходы не смягчаются."),
    ("Добавка к каннибализации, п.п.", [0, 0, 0, 0, 0.15, 0, -0.05], PCT,
     "В пессимистичном сценарии новый продукт съедает старый сильнее."),
    ("Множитель оттока", [1, 1, 1, 1, 1.5, 1.0, 0.8], PCT,
     "Выше отток — короче жизнь клиента — меньше LTV."),
]
r = 5
for label, vals, fmt, note in LEVERS:
    m.cell(r, 1, label).font = BOLD
    for i, v in enumerate(vals, 2):
        c = m.cell(r, i, v); c.font = BLUE; c.number_format = fmt; c.fill = FILL_IN; c.border = THIN
    m.cell(r, 10, note).font = NOTE
    r += 1

MR = {}
def block(title):
    global r
    r += 1
    c = m.cell(r, 1, title); c.font = BOLD; c.fill = PatternFill("solid", fgColor="D9E2F3")
    for i in range(2, 9):
        m.cell(r, i).fill = PatternFill("solid", fgColor="D9E2F3")
    r += 1

def row(key, label, tpl, fmt=NUM, note="", res=False):
    """tpl — шаблон формулы, {c} заменяется на букву колонки варианта."""
    global r
    m.cell(r, 1, label).font = BOLD if res else BLACK
    for i in range(2, 9):
        col = gcl(i)
        c = m.cell(r, i, "=" + tpl.format(c=col))
        c.font = BLACK; c.number_format = fmt; c.border = THIN
        if res:
            c.fill = FILL_RES; c.font = BOLD
    if note:
        m.cell(r, 10, note).font = NOTE
    MR[key] = r
    r += 1

MASK, KCONV, KMARG, KCANN, KCHURN = 5, 6, 7, 8, 9

block("МЕДИА И ОХВАТ")
row("budget", "Медиабюджет", f'SUMIF({CP},">="&{{c}}${MASK},{CD})', CUR,
    "Сумма бюджетов каналов, попавших в этот уровень ФРК.")
row("impr", "Показы, всего", f'SUMPRODUCT(({CP}>={{c}}${MASK})*{CJ})', NUM,
    "Сумма показов включённых каналов.")
row("reach_s", "Комбинированный охват, % ЦА", f'1-EXP(SUMPRODUCT(({CP}>={{c}}${MASK})*{CQ}))', PCT,
    "1 − Π(1 − охват_i). Каналы пересекаются, поэтому охваты НЕ складываются.")
row("reach_a", "Охват, чел", f'{{c}}{MR["reach_s"]}*{V["aud"]}', NUM)
row("freq", "Средняя частота", f'IF({{c}}{MR["reach_a"]}>0,{{c}}{MR["impr"]}/{{c}}{MR["reach_a"]},0)', NUM2,
    "Показы / охваченные люди. Норма 3–6. Выше 10 — деньги уходят в переконтакт.")

block("ВОРОНКА")
row("apps_d", "Заявки прямые (perform + internal)",
    f'SUMPRODUCT(({CP}>={{c}}${MASK})*{CM})*{{c}}${KCONV}', NUM,
    "Показы × CTR × CR по каждому каналу, затем множитель сценария.")
row("uplift", "Суммарный brand-uplift", f'SUMPRODUCT(({CP}>={{c}}${MASK})*{CN})*{{c}}${KCONV}', PCT2,
    "Сколько процентов прибавляет охватная реклама к органике.")
row("apps_b", "Заявки от brand-lift",
    f'{V["base_apps"]}*{{c}}{MR["uplift"]}*({CAMP}+{V["halo_m"]}*{V["halo_d"]})', NUM,
    "Органика × uplift × (месяцы кампании + halo × затухание). САМОЕ недоказуемое звено модели.")
row("apps", "Заявки, всего", f'{{c}}{MR["apps_d"]}+{{c}}{MR["apps_b"]}', NUM)
row("appr", "Одобрено", f'{{c}}{MR["apps"]}*{V["approval"]}', NUM)
row("act", "Активировано", f'{{c}}{MR["appr"]}*{V["activation"]}', NUM)
row("cann", "Каннибализация, %", f'MIN(0.95,MAX(0,{V["cannib"]}+{{c}}${KCANN}))', PCT)
row("inc", "ИНКРЕМЕНТАЛЬНЫЕ КЛИЕНТЫ",
    f'{{c}}{MR["act"]}*(1-{{c}}{MR["cann"]})*(1-{V["deadweight"]})', NUM,
    "Активировано × (1 − каннибализация) × (1 − deadweight). Это и есть честный результат.", res=True)
row("acq_total", "Выплаты за привлечение, всего", f'{{c}}{MR["act"]}*{V["acq"]}', CUR,
    "База — АКТИВИРОВАНО, а не инкремент: платим каждому сработавшему, включая тех, "
    "кто пришёл бы и без стимула. Главная ловушка промо-механик.")
row("acq_per_inc", "Выплата на инкрементального клиента",
    f'IF({{c}}{MR["inc"]}>0,{{c}}{MR["acq_total"]}/{{c}}{MR["inc"]},"")', CUR,
    "Фактическая цена одного ИНКРЕМЕНТАЛЬНОГО клиента. Всегда выше номинала выплаты.")

block("ЭКОНОМИКА ОДНОГО КЛИЕНТА")
row("rev", "Доход в месяц",
    f'({V["balance"]}*{V["nim"]}/12+{V["turnover"]}*{V["interchange"]}+{V["fee"]})*{{c}}${KMARG}', CURM,
    "Баланс×NIM/12 + оборот×интерчейндж + комиссии. Множитель маржи бьёт только сюда.")
row("cost_c", "Расход в месяц",
    f'{V["turnover"]}*{V["cashback"]}+{V["servicing"]}+{V["balance"]}*{V["cor"]}/12', CURM,
    "Кэшбэк + обслуживание + стоимость риска. Расходы в сценариях не смягчаются — это консервативно.")
row("marg", "Маржа на клиента в месяц (полная)", f'{{c}}{MR["rev"]}-{{c}}{MR["cost_c"]}', CURM,
    "В первые месяцы умножается на МИН(1; месяц/ramp-up).")
row("value_pc", "Ценность клиента (готовая)", f'{V["value_pc"]}*{{c}}${KMARG}', CUR,
    "Только для режима value_per_client. Множитель маржи к ней применяется, множитель оттока — нет: "
    "отток уже внутри присланного числа, иначе пессимизм считался бы дважды.")
row("churn_v", "Отток в месяц", f'MIN(0.9,{V["churn"]}*{{c}}${KCHURN})', PCT)
row("disc_m", "Месячная ставка дисконта", f'(1+{V["disc"]})^(1/12)-1', PCT2)

# ---------------------------------------------------------------- P&L лист
pl = wb.create_sheet("Расчет_PL")
BLOCK_W = 7
STARTS = [2 + i * BLOCK_W for i in range(7)]
pl["A1"] = "ПОМЕСЯЧНЫЙ P&L — по одному блоку на каждый вариант"
pl["A1"].font = H1
pl["A2"] = ("Всё считается формулами от листов «Ввод» и «Модель». Горизонт больше 36 месяцев — "
            "протяните строки вниз. Строки за пределами горизонта автоматически обнуляются.")
pl["A2"].font = NOTE
pl.merge_cells("A2:H2")
pl.column_dimensions["A"].width = 9
pl.cell(4, 1, "Месяц").font = H2
pl.cell(4, 1).fill = FILL_H
sub = ["Активные клиенты", "Валовая маржа", "Затраты", "Net", "Кумулятив", "Дисконт. Net", "Мес. окупаемости"]
for bi, s0 in enumerate(STARTS):
    pl.merge_cells(start_row=3, start_column=s0, end_row=3, end_column=s0 + BLOCK_W - 1)
    c = pl.cell(3, s0, VAR[bi]); c.font = H2; c.fill = FILL_H
    c.alignment = Alignment(horizontal="center")
    for j, s in enumerate(sub):
        c = pl.cell(4, s0 + j, s); c.font = H2; c.fill = FILL_H; c.border = THIN
        c.alignment = Alignment(wrap_text=True, vertical="center")
        pl.column_dimensions[gcl(s0 + j)].width = 14
pl.row_dimensions[4].height = 30

MONTHS = 36
for k in range(MONTHS):
    row_i = 5 + k
    pl.cell(row_i, 1, k + 1).font = BOLD
    for bi, s0 in enumerate(STARTS):
        mc = gcl(2 + bi)                      # колонка варианта на листе «Модель»
        a, mg, co, nt, cu, dc, pb = [gcl(s0 + j) for j in range(BLOCK_W)]
        prev_a = f"{a}{row_i-1}" if k else "0"
        prev_cu = f"{cu}{row_i-1}" if k else "0"
        guard = f'IF($A{row_i}>{V["horizon"]},0,'
        f_active = (f'={guard}{prev_a}*(1-Модель!${mc}${MR["churn_v"]})'
                    f'+IF($A{row_i}<={CAMP},Модель!${mc}${MR["inc"]}/{CAMP},0))')
        f_marg = (f'={guard}IF({V["mode"]}="value_per_client",'
                  f'IF($A{row_i}<={CAMP},Модель!${mc}${MR["inc"]}/{CAMP},0)*Модель!${mc}${MR["value_pc"]},'
                  f'{a}{row_i}*Модель!${mc}${MR["marg"]}*MIN(1,$A{row_i}/{RAMP})))')
        f_cost = (f'={guard}IF($A{row_i}<={CAMP},'
                  f'(Модель!${mc}${MR["budget"]}+Модель!${mc}${MR["acq_total"]})/{CAMP},0)'
                  f'+IF($A{row_i}=1,{V["production"]}+{V["itdev"]}+{V["other"]},0)+{V["opex"]})')
        f_net = f'={mg}{row_i}-{co}{row_i}'
        f_cum = f'={prev_cu}+{nt}{row_i}'
        f_dsc = f'={guard}{nt}{row_i}/(1+Модель!${mc}${MR["disc_m"]})^$A{row_i})'
        # `{mg}` в условии — та же защита, что `gm_cum > 0` в движке: при пустом медиаплане
        # накопленный поток равен нулю уже в первом месяце, и книга рапортовала
        # «окупаемость 1 месяц» там, где не происходило вообще ничего.
        f_pb = (f'=IF({V["mode"]}="value_per_client","",'
                f'IF($A{row_i}>{V["horizon"]},"",'
                f'IF(AND({cu}{row_i}>=0,SUM(${mg}$5:{mg}{row_i})>0),$A{row_i},"")))')
        for col, f_, fmt in ((a, f_active, NUM), (mg, f_marg, CUR), (co, f_cost, CUR),
                             (nt, f_net, CUR), (cu, f_cum, CUR), (dc, f_dsc, CUR), (pb, f_pb, NUM)):
            c = pl[f"{col}{row_i}"]; c.value = f_; c.font = BLACK; c.number_format = fmt; c.border = THIN
pl.freeze_panes = "B5"
LASTR = 4 + MONTHS

# ------------------------------------------------- результаты обратно в Модель
def plcol(j):
    """Ссылка на колонку j блока варианта {c} — подставляется по индексу колонки Модели."""
    return {gcl(2 + bi): f"Расчет_PL!${gcl(STARTS[bi]+j)}$5:${gcl(STARTS[bi]+j)}${LASTR}" for bi in range(7)}

SUMS = {j: plcol(j) for j in range(BLOCK_W)}

def row_pl(key, label, j, agg="SUM", fmt=CUR, note="", res=False):
    global r
    m.cell(r, 1, label).font = BOLD if res else BLACK
    for i in range(2, 9):
        col = gcl(i)
        c = m.cell(r, i, f"={agg}({SUMS[j][col]})")
        c.font = GREEN; c.number_format = fmt; c.border = THIN
        if res:
            c.fill = FILL_RES; c.font = Font(name=F, size=10, bold=True, color="008000")
    if note:
        m.cell(r, 10, note).font = NOTE
    MR[key] = r
    r += 1

block("РЕЗУЛЬТАТ ЗА ГОРИЗОНТ")
row_pl("gm", "Валовая маржа", 1, note="Сумма помесячной маржи с листа «Расчет_PL».")
row_pl("tc", "Затраты, всего", 2, note="Медиа + производство + IT + разовые + opex за весь горизонт.")
row("net", "Чистый эффект (недисконт.)", f'{{c}}{MR["gm"]}-{{c}}{MR["tc"]}', CUR)
row_pl("npv", "NPV", 5, note="Сумма дисконтированных Net. ГЛАВНАЯ ЦИФРА решения.", res=True)
row("payback", "Окупаемость, мес",
    'IFERROR(IF(MIN({pb})=0,"нет",MIN({pb})),"нет")'.replace("{pb}", "PBRANGE"), NUM,
    "Первый месяц, в котором накопленный поток вышел в плюс.")
# payback требует индивидуального диапазона на вариант
for i in range(2, 9):
    bi = i - 2
    rng = f"Расчет_PL!${gcl(STARTS[bi]+6)}$5:${gcl(STARTS[bi]+6)}${LASTR}"
    m.cell(MR["payback"], i).value = (
        f'=IF({V["mode"]}="value_per_client","не считается",'
        f'IF(MIN({rng})=0,"нет",MIN({rng})))')
    m.cell(MR["payback"], i).font = GREEN

block("ЭФФЕКТ В РАЗНЫХ ВАЛЮТАХ")
row_pl("akb_peak", "АКБ — пик активной базы, чел", 0, agg="MAX", fmt=NUM,
       note="Максимум активных клиентов за горизонт. Валюта маркетинга.")
row_pl("cl_months", "Клиенто-месяцев", 0, agg="SUM", fmt=NUM,
       note="Сумма активных по месяцам. База для пересчёта годового ОД в эффект за горизонт.")
row("od_total", "ОД за горизонт", f'{{c}}{MR["cl_months"]}*{V["od"]}/12', CUR,
    "Клиенто-месяцы × годовой ОД / 12. ДОХОД, а не маржа: с NPV несопоставим напрямую.")
row("aum_peak", "AUM / доп. объём средств, пик", f'{{c}}{MR["akb_peak"]}*{V["balance"]}', CUR,
    "Пик активной базы × средний баланс. Валюта розницы.")

block("МЕТРИКИ ЭФФЕКТИВНОСТИ")
row("mkt", "Маркетинговые затраты",
    f'{{c}}{MR["budget"]}+{V["production"]}+{{c}}{MR["acq_total"]}', CUR,
    "Медиа + производство + выплаты за привлечение. IT-разработка и opex сюда НЕ входят.")
row("romi", "ROMI", f'IF({{c}}{MR["mkt"]}>0,({{c}}{MR["gm"]}-{{c}}{MR["mkt"]})/{{c}}{MR["mkt"]},"")', PCT,
    "(Валовая маржа − маркетинг) / маркетинг. Для запуска продукта НЕ показателен: не видит IT и opex.")
row("cpa", "Инкрементальный CPA", f'IF({{c}}{MR["inc"]}>0,{{c}}{MR["mkt"]}/{{c}}{MR["inc"]},"")', CUR,
    "Стоимость одного ИНКРЕМЕНТАЛЬНОГО клиента, а не любой заявки.")
row("ltv", "LTV на клиента (за горизонт)", f'IF({{c}}{MR["inc"]}>0,{{c}}{MR["gm"]}/{{c}}{MR["inc"]},"")', CUR)
row("ltvcac", "LTV / CAC", f'IF({{c}}{MR["cpa"]}>0,{{c}}{MR["ltv"]}/{{c}}{MR["cpa"]},"")', NUM2,
    "Норма ≥ 3. Ниже — либо горизонт занижен, либо экономика не тянет такой CAC.")
row("be", "Клиентов для безубыточности",
    f'IF(AND({{c}}{MR["inc"]}>0,{{c}}{MR["gm"]}>0),{{c}}{MR["tc"]}/({{c}}{MR["gm"]}/{{c}}{MR["inc"]}),"")', NUM)
# Движок отвечает не только «окупится ли названный бюджет», но и «какой бюджет допустим».
# Это самый частый вопрос комитета, и книга на него молчала.
row("max_cpa", "Предельный CPA",
    f'IF(AND({{c}}{MR["inc"]}>0,{V["hurdle"]}>0),{{c}}{MR["ltv"]}/{V["hurdle"]},"")', CUR,
    "Ценность клиента, делённая на норму LTV/CAC. Больше этого платить за привлечение нельзя.")
row("budget_max", "Бюджет при предельном CPA",
    f'IF({{c}}{MR["max_cpa"]}="","",{{c}}{MR["inc"]}*{{c}}{MR["max_cpa"]})', CUR,
    "Сколько можно потратить на этот объём клиентов, не нарушив норму.")

block("ЛЕСТНИЦА ФРК — окупается ли шаг вверх")
def ladder(key, label, tpl, fmt, note=""):
    global r
    m.cell(r, 1, label).font = BLACK
    m.cell(r, 2, "—").font = BLACK
    m.cell(r, 2).alignment = Alignment(horizontal="center")
    for i in range(3, 6):                      # только ФРК 3, 2, 1
        prev, cur = gcl(i - 1), gcl(i)
        c = m.cell(r, i, "=" + tpl.format(p=prev, c=cur))
        c.font = BLACK; c.number_format = fmt; c.border = THIN
    for i in range(6, 9):
        m.cell(r, i, "").font = BLACK
    if note:
        m.cell(r, 10, note).font = NOTE
    MR[key] = r
    r += 1

ladder("d_budget", "Предельный бюджет шага", f'{{c}}{MR["budget"]}-{{p}}{MR["budget"]}', CUR,
       "Сколько дополнительных денег стоит переход на этот уровень.")
ladder("d_cl", "Предельные клиенты шага", f'{{c}}{MR["inc"]}-{{p}}{MR["inc"]}', NUM)
ladder("d_cpa", "Предельный CPA шага",
       f'IF(({{c}}{MR["inc"]}-{{p}}{MR["inc"]})>0,({{c}}{MR["budget"]}-{{p}}{MR["budget"]})/({{c}}{MR["inc"]}-{{p}}{MR["inc"]}),"")',
       CUR, "Во сколько обходится клиент, купленный ИМЕННО этим шагом. Сравнивать с LTV, а не со средним CPA.")
ladder("d_npv", "Предельный NPV шага", f'{{c}}{MR["npv"]}-{{p}}{MR["npv"]}', CUR)
ladder("worth", "Шаг окупается?", f'IF({{c}}{MR["npv"]}-{{p}}{MR["npv"]}>0,"ДА","НЕТ")', "General",
       "Если НЕТ — уровень выше уничтожает стоимость. Это главный вывод по выбору ФРК.")

block("RICE (для сравнения инициатив между собой)")
row("rice", "RICE-скор",
    f'IF({V["effort"]}>0,$G${MR["inc"]}*{V["impact"]}*{V["conf"]}/{V["effort"]},"")', NUM2,
    "Reach × Impact × Confidence / Effort. Reach = инкремент базового сценария. Сравнивать только с другими инициативами.")
row("npv_pm", "NPV на чел-месяц",
    f'IF({V["effort"]}>0,$G${MR["npv"]}/{V["effort"]},"")', CUR,
    "Честнее RICE при сравнении: сразу в деньгах на единицу дефицитного ресурса.")
m.freeze_panes = "B5"

# ================================================================== ИТОГ
it = wb.create_sheet("Итог")
it.column_dimensions["A"].width = 38
for i in range(2, 7):
    it.column_dimensions[gcl(i)].width = 17
it.column_dimensions["G"].width = 60
it["A1"] = "ИТОГ"
it["A1"].font = H1
it["A2"] = f'={V["name"]}'
it["A2"].font = Font(name=F, size=12, bold=True, color="008000")
NPV_SC = f'Модель!F{MR["npv"]}:H{MR["npv"]}'          # пессим / база / оптим
# Инверсия: чем больше клиентов, тем хуже NPV. Пессимистичный сценарий тогда НЕ худший,
# поэтому вердикт строится на МИН/МАКС по сценариям, а не на колонке «Пессимистичный».
INVERTED = f'AND(Модель!H{MR["npv"]}<Модель!G{MR["npv"]},Модель!G{MR["npv"]}<Модель!F{MR["npv"]})'

IDX = V["conf_idx"]
# Явное перечисление, а не диапазон строк: после вставки B− диапазон gA:gD захватил
# новую строку по счастливой случайности порядка, и это держалось бы до первой перестановки.
GTOT = "(" + "+".join(f'Ввод!$B${R[_GRADE_CELL[g]]}' for g in calc.GRADE_W) + ")"
# Индекс уверенности < 0,6 — вердикт не может быть «GO» ни при каком NPV. То же правило
# в src/calc.py: main() → downgraded_by_confidence.
DOWNGRADE = f'AND(ISNUMBER({IDX}),{IDX}<0.6,LEFT($B$5,2)="GO")'

# Кредитный продукт: список синхронизирован с calc.CREDIT_PRODUCTS
IS_CREDIT = "OR(" + ",".join(f'{V["product"]}="{p}"' for p in sorted(calc.CREDIT_PRODUCTS)) + ")"

it["A4"] = "ВЕРДИКТ"
it["A4"].font = BOLD
it["B4"] = (f'=IF({DOWNGRADE},'
            f'"СНАЧАЛА ПИЛОТ — по деньгам вышло GO, но индекс уверенности "&TEXT({IDX},"0.00")&'
            f'" (порог 0,6): из "&{GTOT}&" допущений "&{V["gD"]}&" — догадки, "&{V["gC"]}&'
            f'" — рыночные бенчмарки. Сначала добрать данные или пилот, затем пересчитать",'
            f'$B$5)')
it["B4"].font = Font(name=F, size=11, bold=True)
it.merge_cells("B4:G4")

it["A5"] = "Вердикт по NPV (до поправки на качество данных)"
it["A5"].font = NOTE
it["B5"] = (f'=IF(AND({INVERTED},MAX({NPV_SC})<=0),'
            f'"NO-GO по экономике юнита — масштаб делает только хуже, переработать выплату/триггер/источник маржи",'
            f'IF(MIN({NPV_SC})>0,"GO — положительный NPV во всех сценариях",'
            f'IF(Модель!G{MR["npv"]}>0,"GO с контролем — база в плюсе, худший сценарий нет: зафиксировать stop-loss и чекпоинты",'
            f'"NO-GO / переработать — базовый сценарий не окупается")))')
it["B5"].font = NOTE
it.merge_cells("B5:G5")

it["A6"] = "СЦЕНАРИИ (полный план)"
it["A6"].font = H2; it["A6"].fill = FILL_H
for i, lbl in enumerate(["Пессимистичный", "Базовый", "Оптимистичный"], 2):
    c = it.cell(6, i, lbl); c.font = H2; c.fill = FILL_H; c.alignment = Alignment(horizontal="center")
rows_sc = [("Инкрементальные клиенты", MR["inc"], NUM), ("NPV", MR["npv"], CUR),
           ("ROMI", MR["romi"], PCT), ("Инкрементальный CPA", MR["cpa"], CUR),
           ("Окупаемость, мес", MR["payback"], NUM), ("LTV / CAC", MR["ltvcac"], NUM2)]
for k, (lbl, mrow, fmt) in enumerate(rows_sc):
    rr = 7 + k
    it.cell(rr, 1, lbl).font = BLACK
    for i, sc in enumerate(["F", "G", "H"], 2):
        c = it.cell(rr, i, f"=Модель!{sc}{mrow}"); c.font = GREEN; c.number_format = fmt; c.border = THIN

it["A14"] = "ЛЕСТНИЦА ФРК"
it["A14"].font = H2; it["A14"].fill = FILL_H
for i, lbl in enumerate(["ФРК 4", "ФРК 3", "ФРК 2", "ФРК 1"], 2):
    c = it.cell(14, i, lbl); c.font = H2; c.fill = FILL_H; c.alignment = Alignment(horizontal="center")
rows_l = [("Медиабюджет", MR["budget"], CUR), ("Охват, % ЦА", MR["reach_s"], PCT),
          ("Средняя частота", MR["freq"], NUM2), ("Инкрементальные клиенты", MR["inc"], NUM),
          ("NPV", MR["npv"], CUR), ("Предельный CPA шага", MR["d_cpa"], CUR),
          ("Предельный NPV шага", MR["d_npv"], CUR), ("Шаг окупается?", MR["worth"], "General")]
for k, (lbl, mrow, fmt) in enumerate(rows_l):
    rr = 15 + k
    it.cell(rr, 1, lbl).font = BLACK
    for i, sc in enumerate(["B", "C", "D", "E"], 2):
        c = it.cell(rr, i, f"=Модель!{sc}{mrow}"); c.font = GREEN; c.number_format = fmt; c.border = THIN
it["A23"] = "Рекомендуемый уровень"
it["A23"].font = BOLD
it["B23"] = (f'="ФРК "&INDEX({{4;3;2;1}},MATCH(MAX(Модель!B{MR["npv"]}:E{MR["npv"]}),'
             f'Модель!B{MR["npv"]}:E{MR["npv"]},0))')
it["B23"].font = Font(name=F, size=11, bold=True, color="008000")

it["A25"] = "ПРОВЕРКА ВВОДНЫХ — тихие ошибки, при которых модель считает, но считает не то"
it["A25"].font = H2; it["A25"].fill = FILL_H
it.merge_cells("A25:G25")
VALID = [
    (f'IF({V["horizon"]}<{V["camp_m"]},"⛔ Горизонт "&{V["horizon"]}&" мес короче кампании "&{V["camp_m"]}&'
     f'" мес: в расчёт попадёт лишь часть медиабюджета и притока — цифры занижены.","")'),
    (f'IF({V["aud"]}<=0,"⛔ Размер ЦА = 0: охват, частота и весь brand-эффект молча обнулились, '
     f'а прямые заявки остались. Результат выглядит правдоподобно и при этом неверен.","")'),
    (f'IF(AND({V["base_apps"]}<=0,COUNTIF(Каналы!$B${FIRST}:$B${LAST},"brand")>0),'
     f'"⛔ Есть охватные каналы, но органический поток заявок = 0. Brand-эффект считается как прирост к органике '
     f'— весь охватный бюджет даёт ровно 0 заявок.","")'),
    # Флаг разведён по режимам, как в движке. Раньше книга безусловно утверждала, что
    # нулевой отток завышает NPV, — на шести кейсах из шестнадцати это была неправда:
    # в режиме готовой ценности отток уже внутри присланного числа и на NPV не влияет.
    # Завышает он там АКБ, ОД и AUM, которые считаются по живой кривой активной базы.
    (f'IF({V["churn"]}>0,"",'
     f'IF({V["mode"]}="value_per_client",'
     f'IF(OR({V["od"]}>0,{V["balance"]}>0),'
     f'"⛔ Отток = 0. На NPV это не влияет (отток внутри ценности клиента), но АКБ, ОД и AUM '
     f'считаются по активной базе — при нулевом оттоке она не убывает, и эти три цифры завышены.",""),'
     f'"⛔ Отток = 0, клиент живёт вечно. LTV и NPV завышены — поставьте хотя бы 1–2% в месяц."))'),
    (f'IF(COUNTIF(Каналы!$R${FIRST}:$R${LAST},"БЮДЖЕТ ВПУСТУЮ*")>0,"⛔ Каналов, тратящих бюджет '
     f'впустую: "&COUNTIF(Каналы!$R${FIRST}:$R${LAST},"БЮДЖЕТ ВПУСТУЮ*")&" — см. колонку R листа '
     f'«Каналы». Чаще всего это неверный тип канала или незаполненные конверсии.","")'),
    (f'IF(COUNTIF(Каналы!$R${FIRST}:$R${LAST},"НАСЫЩЕНИЕ*")>0,"⛔ Насыщено каналов: "&'
     f'COUNTIF(Каналы!$R${FIRST}:$R${LAST},"НАСЫЩЕНИЕ*")&" — см. колонку R листа «Каналы». '
     f'Дополнительный бюджет в эти каналы не даёт заявок вообще.","")'),
]
for k, f_ in enumerate(VALID):
    c = it.cell(26 + k, 1, "=" + f_); c.font = Font(name=F, size=10, bold=True, color="C00000")
    it.merge_cells(start_row=26 + k, start_column=1, end_row=26 + k, end_column=7)

it["A32"] = "КРАСНЫЕ ФЛАГИ"
it["A32"].font = H2; it["A32"].fill = FILL_H
it.merge_cells("A32:G32")
FLAGS = [
    (f'IF({DOWNGRADE},"⚑ ВЕРДИКТ ПОНИЖЕН: индекс уверенности "&TEXT({IDX},"0.00")&'
     f'" < 0,6. Цифра держится на допущениях грейда C и D — подтвердите ключевые из них, '
     f'иначе решение принимается не по модели, а по догадке.","")'),
    (f'IF({INVERTED},"⚑ ИНВЕРСИЯ СЦЕНАРИЕВ: чем БОЛЬШЕ клиентов приводит инициатива, тем ХУЖЕ NPV. '
     f'Маржа клиента меньше стоимости его привлечения — каждый следующий клиент увеличивает убыток. '
     f'Ни объём, ни рост конверсии этого не чинят: менять надо выплату, триггер или источник маржи.","")'),
    # Зеркало проверки движка: ROMI считается к маркетинговым затратам, а они здесь —
    # малая часть общих. Книга показывает ROMI так же заметно, как приложение, поэтому
    # оговорка нужна и здесь: иначе стейкхолдер увидит в книге положительную отдачу
    # на инициативе, которая уничтожает деньги.
    (f'IF(AND(Модель!G{MR["romi"]}<>"",Модель!G{MR["tc"]}>0,'
     f'Модель!G{MR["mkt"]}/Модель!G{MR["tc"]}<0.5),'
     f'"⚑ ROMI СЧИТАЕТСЯ НЕ КО ВСЕМ ЗАТРАТАМ: в его знаменателе только маркетинг — "&'
     f'TEXT(Модель!G{MR["mkt"]},"#,##0")&" ₽ из "&TEXT(Модель!G{MR["tc"]},"#,##0")&'
     f'" ₽, то есть "&TEXT(Модель!G{MR["mkt"]}/Модель!G{MR["tc"]},"0%")&'
     f'". IT-разработка, постоянные расходы и прочие разовые в него не входят, хотя '
     f'в NPV входят полностью.","")'),
    # Зеркало проверки движка: платим за клиента, а deadweight не задан. Формулировка
    # обязана совпадать с `flags()` — расхождение здесь означает, что книга и код дают
    # разные предупреждения на одних данных, и сверка это ловит.
    (f'IF(AND({V["acq"]}>0,{V["deadweight"]}<=0),"⚑ DEADWEIGHT = 0 ПРИ ВЫПЛАТЕ ЗА КЛИЕНТА: '
     f'подарок или бонус получают все, кто сработал, включая тех, кто оформил бы и без него. '
     f'«Скидка дала +40%» бессмысленно, если половина взяла бы и так — а при нуле модель '
     f'считает, что не взял бы никто. Это самое дорогое допущение акции, и оно сейчас '
     f'не задано, а не подтверждено.","")'),
    (f'IF(Модель!G{MR["acq_total"]}>0,"⚑ Выплаты идут за "&TEXT(Модель!G{MR["act"]},"#,##0")&'
     f'" сработавших клиентов, а инкремент — только "&TEXT(Модель!G{MR["inc"]},"#,##0")&'
     f'". За разницу вы платите, ничего не получая. Фактическая выплата на одного инкрементального клиента — "&'
     f'TEXT(Модель!G{MR["acq_per_inc"]},"#,##0")&" ₽.","")'),
    (f'IF(Модель!E{MR["freq"]}>12,"⚑ Частота "&TEXT(Модель!E{MR["freq"]},"0.0")&'
     f'" контактов — сильный перебор (эффективная 3–6). Бюджет уходит в переконтакт.","")'),
    (f'IF(Модель!E{MR["reach_s"]}>0.95,"⚑ Охват упёрся в потолок ЦА — рост бюджета даёт только частоту, не новых людей.","")'),
    (f'IF(Модель!G{MR["apps"]}>0,IF(Модель!G{MR["apps_b"]}/Модель!G{MR["apps"]}>0.4,'
     f'"⚑ "&TEXT(Модель!G{MR["apps_b"]}/Модель!G{MR["apps"]},"0%")&'
     f'" заявок держится на brand-uplift — самое недоказуемое допущение. Нужен geo-holdout или MMM.",""),"")'),
    (f'IF(AND(ISNUMBER(Модель!G{MR["ltvcac"]}),Модель!G{MR["ltvcac"]}<3),'
     f'"⚑ LTV/CAC = "&TEXT(Модель!G{MR["ltvcac"]},"0.00")&" (норма ≥3). Горизонт занижен или экономика не тянет такой CAC.","")'),
    (f'IF(Модель!G{MR["payback"]}="нет","⚑ Окупаемости на заданном горизонте нет. Проверьте горизонт 36 мес.","")'),
    # Флаг только для кредитных продуктов: на дебетовых CoR законно равен нулю.
    # Условие обязано совпадать с CREDIT_PRODUCTS в calc.py — иначе книга и движок
    # выдают разные предупреждения на одних данных, и сверка это ловит.
    (f'IF(AND({IS_CREDIT},{V["cor"]}=0,Модель!G{MR["marg"]}>0),'
     f'"⚑ Cost of Risk = 0. Для кредитных продуктов это делает модель заведомо неверной.","")'),
    (f'IF({V["cannib"]}=0,"⚑ Каннибализация = 0. В банке новый продукт почти всегда ест старый — обоснуйте ноль.","")'),
]
FLAG_ROW = 33
for k, f_ in enumerate(FLAGS):
    c = it.cell(FLAG_ROW + k, 1, "=" + f_); c.font = Font(name=F, size=10, color="C00000")
    it.merge_cells(start_row=FLAG_ROW + k, start_column=1, end_row=FLAG_ROW + k, end_column=7)

rr = FLAG_ROW + len(FLAGS) + 1          # едет вместе со списком флагов, а не прибит к строке 42
it.cell(rr, 1, "RICE-скор").font = BOLD
it.cell(rr, 2, f"=Модель!B{MR['rice']}").font = GREEN
it.cell(rr, 2).number_format = NUM2
it.cell(rr + 1, 1, "NPV на чел-месяц").font = BOLD
it.cell(rr + 1, 2, f"=Модель!B{MR['npv_pm']}").font = GREEN
it.cell(rr + 1, 2).number_format = CUR

ORDER = ["Итог", "Ввод", "Сегменты", "Каналы", "Модель", "Расчет_PL", "Методика"]
wb._sheets = [wb[s] for s in ORDER if s in wb.sheetnames]

# ================================================================ МЕТОДИКА
me = wb.create_sheet("Методика")
me.column_dimensions["A"].width = 34
me.column_dimensions["B"].width = 62
me.column_dimensions["C"].width = 62
me["A1"] = "МЕТОДИКА — все формулы модели"
me["A1"].font = H1
for i, h in enumerate(["Шаг", "Формула", "Смысл, допущения и где ломается"], 1):
    c = me.cell(3, i, h); c.font = H2; c.fill = FILL_H; c.border = THIN

METH = [
 ("0. Возрастные сегменты", "ЦА_эфф = Размер × Достижимая доля;  ставки взвешиваются по своей базе",
  "Лист «Сегменты». Один прогон по взвешенным допущениям, а не N прогонов: воронка взвешивается по "
  "эффективной аудитории, экономика клиента — по числу клиентов, ставки (NIM, CoR) — по балансу, "
  "интерчейндж и кэшбэк — по обороту. Поэтому баланс×NIM и оборот×интерчейндж равны посегментной "
  "сумме ТОЧНО. Отток и ramp-up усредняются приблизительно — у них нет такой базы."),
 ("0а. Доступность продукта по возрасту", "Доля доступных возрастов = (возраст_макс − порог + 1) / лет в сегменте",
  "Правила банка: счёт и вклад до 14 лет открывает третье лицо в офисе по свидетельству о рождении; "
  "с 6 лет — детская карта и копилки в детском приложении; с 14 — вход в приложение, накопительные "
  "продукты, брокерский счёт, подписка и лояльность; с 18 — весь функционал банка и экосистемы. "
  "Доля считается при равномерном распределении по годам — замените своей демографией."),
 ("1. Показы канала", "Показы = Бюджет / CPM × 1000",
  "Для internal-каналов вместо этого берётся заданный охват базы. Если планируете в GRP — пересчитайте в показы заранее."),
 ("2. Охват канала", "Охват = 1 − e^(−λ), где λ = Показы / Размер ЦА",
  "Кривая Пуассона: каждый следующий показ всё чаще попадает в уже охваченного. Потолок 98%. "
  "Модель предполагает случайное распределение показов; при узком таргетинге реальный охват будет ниже."),
 ("3. Частота", "Частота = λ / Охват",
  "Среднее число контактов на охваченного человека. Эффективная частота 3–6. Выше 10 — деньги в переконтакт."),
 ("4. Комбинированный охват", "Охват_общий = 1 − Π(1 − Охват_i)",
  "Считается через логарифмы (колонка Q листа «Каналы»). ДОПУЩЕНИЕ: каналы независимы. "
  "Если ТВ и наружка бьют в одну и ту же аудиторию, реальный совокупный охват ниже расчётного."),
 ("5. Прямые заявки", "Заявки = Показы × CTR × CR(клик→заявка) × Множитель сценария",
  "Только для internal и perform. Brand-каналы прямых заявок не дают по определению."),
 ("6. Brand-uplift", "Uplift = Σ (uplift_i × Охват_i × МИН(1; Частота_i / 3))",
  "Охватные каналы работают через прирост органики. Полный эффект — только при частоте 3+. "
  "САМОЕ слабое звено: uplift берётся экспертно и не доказывается last-click атрибуцией. Нужен geo-holdout или MMM."),
 ("7. Заявки от brand", "Заявки_brand = Органика/мес × Uplift × (Месяцы кампании + Halo × Затухание)",
  "Если доля brand-заявок выше 40% от всех — цифра не защищаема без эксперимента. Лист «Итог» это ловит."),
 ("8. Воронка", "Инкремент = Заявки × Approval × Activation × (1 − Каннибализация) × (1 − Deadweight)",
  "Каннибализация — переезд со своих же продуктов. Deadweight — те, кто купил бы и без акции. "
  "Оба обнуляют эффект, но их почти всегда забывают."),
 ("9. Маржа клиента", "Маржа = (Баланс×NIM/12 + Оборот×Интерчейндж + Комиссии) × k_маржи "
                      "− (Оборот×Кэшбэк + Обслуживание + Баланс×CoR/12)",
  "Множитель сценария применяется ТОЛЬКО к доходам — расходы не смягчаются. Это намеренно консервативно. "
  "Cost of Risk обязателен для кредитных продуктов: без него кредитка всегда выглядит прекрасно."),
 ("10. Ramp-up", "Маржа_месяца = Маржа × МИН(1; Номер месяца / Ramp-up)",
  "Клиент не выходит на полную маржу мгновенно: карту надо активировать, зарплату перевести, остаток накопить."),
 ("11. Активные клиенты", "Активные_m = Активные_(m−1) × (1 − Отток) + Приток_m",
  "Приток распределён равномерно по месяцам кампании. Отток геометрический — упрощение: "
  "в реальности он выше в первые месяцы и падает со временем."),
 ("12. Затраты месяца", "Затраты_m = Медиа/Мес.кампании (если m ≤ кампания) + Разовые (если m = 1) + Opex",
  "Разовые = производство + IT-разработка + прочие. Списываются в первый месяц целиком."),
 ("12а. Выплаты за привлечение", "Выплаты = Активировано × Выплата за клиента",
  "База — АКТИВИРОВАНО, а не инкремент: реферальный бонус или промокод получает каждый сработавший, "
  "включая deadweight и переехавших со своих же продуктов. За разницу вы платите, ничего не получая. "
  "Входят в маркетинговые затраты, поэтому бьют и по ROMI, и по CPA."),
 ("13. NPV", "NPV = Σ (Net_m / (1 + r_мес)^m), где r_мес = (1 + r_год)^(1/12) − 1",
  "ГЛАВНАЯ цифра решения. Для запуска продукта смотреть именно на неё, а не на ROMI."),
 ("14. Окупаемость", "Первый месяц, где накопленный НЕдисконтированный поток ≥ 0",
  "Считается по недисконтированному потоку — так принято в маркетинговой отчётности. "
  "Дисконтированная окупаемость будет длиннее."),
 ("15. ROMI", "ROMI = (Валовая маржа − Маркетинговые затраты) / Маркетинговые затраты",
  "Маркетинговые = медиа + производство. IT-разработка и opex НЕ входят. "
  "Поэтому при запуске продукта ROMI может быть отличным при глубоко отрицательном NPV — не выносите его в заголовок."),
 ("16. Инкрементальный CPA", "CPA = Маркетинговые затраты / Инкрементальные клиенты",
  "Знаменатель — именно инкремент, а не все активации. Иначе вы платите себе за органику."),
 ("17. LTV / CAC", "LTV = Валовая маржа / Инкремент;  CAC = CPA",
  "Норма ≥ 3. Значение зависит от горизонта: на 12 мес почти любой банковский продукт выглядит убыточным."),
 ("18. Лестница ФРК", "Предельный CPA = ΔБюджет / ΔКлиенты;  Предельный NPV = NPV_уровня − NPV_предыдущего",
  "Уровни вложены (4 ⊂ 3 ⊂ 2 ⊂ 1), поэтому сравнивать нужно ПРЕДЕЛЬНУЮ отдачу шага, а не средний ROMI уровня. "
  "Отрицательный предельный NPV = уровень выше уничтожает стоимость."),
 ("19. RICE", "RICE = Reach × Impact × Confidence / Effort",
  "Reach = инкремент базового сценария. Осмысленно ТОЛЬКО при сравнении инициатив между собой, "
  "абсолютное значение ни о чём не говорит. «NPV на чел-месяц» для этого честнее."),
 ("20. Сценарии", "Рычаги в строках 5–9 листа «Модель»",
  "Пессимистичный: конверсии ×0,6, доходы ×0,75, каннибализация +15 п.п., отток ×1,5. "
  "Оптимистичный: ×1,3 / ×1,15 / −5 п.п. / ×0,8. Правьте под свою историческую точность прогнозов."),
]
rr = 4
for a, b, c_ in METH:
    me.cell(rr, 1, a).font = BOLD
    me.cell(rr, 2, b).font = Font(name="Consolas", size=9)
    me.cell(rr, 3, c_).font = BLACK
    for i in range(1, 4):
        me.cell(rr, i).alignment = Alignment(wrap_text=True, vertical="top")
        me.cell(rr, i).border = THIN
    me.row_dimensions[rr].height = 46
    rr += 1

me.cell(rr + 1, 1, "ЧЕГО МОДЕЛЬ НЕ УМЕЕТ").font = BOLD
LIM = [
 "Насыщение канала: удвоение бюджета в модели удваивает показы линейно. В реальности после определённого порога "
 "растёт только цена. Если бюджет канала больше исторического в 1,5+ раза — вручную занизьте CTR на 15–30%.",
 "Пересечение аудиторий каналов считается по формуле независимости — при узком общем таргетинге охват завышается.",
 "Отток задан плоским. Реальная кривая: высокий в первые 2–3 месяца, затем выполаживается.",
 "Конкурентная реакция не моделируется: если конкурент выйдет с контр-кампанией, конверсии просядут.",
 "Сезонность не заложена. Запуск в декабре и в июле дадут разный результат при одинаковых вводных.",
 "Ставка дисконтирования одна на всё. Строго говоря, для рискового пилота она должна быть выше.",
 "Сегменты сводятся в один взвешенный прогон. Отток и ramp-up при этом усредняются: если у детей и "
 "молодёжи кривые жизни расходятся сильно, посчитайте сегменты отдельными кейсами и сложите NPV.",
 "Охват считается по объединённой аудитории сегментов. Медиа при этом предполагается одинаково "
 "эффективным на всех возрастах — для детской части это неверно: до неё вы достаёте через родителя.",
]
for k, t in enumerate(LIM):
    c = me.cell(rr + 2 + k, 1, "• " + t); c.font = NOTE
    me.merge_cells(start_row=rr + 2 + k, start_column=1, end_row=rr + 2 + k, end_column=3)
    me.cell(rr + 2 + k, 1).alignment = Alignment(wrap_text=True, vertical="top")
    me.row_dimensions[rr + 2 + k].height = 30

OUT.parent.mkdir(exist_ok=True)
wb.save(OUT)
print(f"Сохранено: {OUT}")
print("Строки листа «Модель» (для tests/check_xlsx_sync.py):",
      {k: v for k, v in sorted(MR.items(), key=lambda x: x[1])})
