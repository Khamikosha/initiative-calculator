#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Реестр параметров: подписи, единицы, дефолты, пояснения.

Зачем отдельным файлом. Диапазоны допустимых значений живут в `calc.SCHEMA` — движок
обязан оставаться самодостаточным, его копия уезжает в навык одним файлом. А подписи,
единицы, дефолты и пояснения нужны формам: веб-путю пользователя, будущей админке
и листу «Ввод». Держать их в HTML — значит завести второй источник истины и разойтись
с движком при первой же правке.

Связь между файлами держится не на честном слове: `_assert_consistent()` при импорте
сверяет множество ключей реестра с `calc.SCHEMA` и падает, если они разошлись.
Новый параметр обязан появиться в обоих местах.

Дальше (BACKLOG 1.1) отсюда же должны генерироваться строки `inp(...)` в build_xlsx.py
и форма админки с переопределением по сегментам.
"""
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

_spec = importlib.util.spec_from_file_location("calc", Path(__file__).with_name("calc.py"))
calc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calc)


@dataclass(frozen=True)
class Param:
    """Один параметр модели: чем он является для человека, а не только для формулы."""
    key: str
    section: str
    label: str
    unit: str = ""
    fmt: str = "num"            # text | int | money | pct | pct2 | num
    default: object = 0
    grade: str = "C"            # предполагаемый грейд источника, пока не заменён своим фактом
    segment_scoped: bool = False    # можно переопределить для отдельного возрастного сегмента
    key_assumption: bool = False    # сильнее прочих двигает результат — подтверждать первым
    types: tuple = ()           # типы инициатив, где параметр работает; пусто — во всех
    kinds: tuple = ()           # для каналов: типы каналов, где параметр работает
    typical: tuple = ()         # обычный диапазон значений — подсказка, а не ограничение
    note: str = ""
    # Структура (список долей, корзины, вычеты), а не одно число: в форму такое
    # не помещается и задаётся конфигом. Форма такие поля пропускает.
    config_only: bool = False

    @property
    def hint(self) -> str:
        """Подсказка в поле: как ЗАПИСАТЬ значение и какое обычно бывает.

        Диапазон в `rng` — границы физического смысла, они ловят 15 вместо 0,15.
        Но 0,15 вместо 0,015 проходит любую валидацию и завышает конверсию вдесятеро
        молча. Спасает только подсказка перед глазами в момент ввода: пример записи
        и типовой порядок величины.
        """
        if self.fmt in ("pct", "pct2"):
            if self.typical:
                lo, hi = self.typical
                return f"обычно {_pc(lo)}–{_pc(hi)} — вводить долей: {_frac(hi)}"
            return "вводить долей: 1,5% это 0,015"
        if self.typical:
            lo, hi = self.typical
            return f"обычно {_num_hint(lo)}–{_num_hint(hi)}"
        return ""

    @property
    def rng(self) -> Optional[Tuple[float, float]]:
        # Каналы — список, а не раздел конфига, поэтому их диапазоны лежат отдельно;
        # без этой ветки rng молча возвращал None для каждого параметра канала.
        # `or {}` — для вложенных разделов (brief, pilot, linked_client, companion,
        # partner): в SCHEMA у них None, плоских диапазонов нет, и .get() падал бы.
        src = calc.CHANNEL_KEYS if self.section == "channels" else calc.SCHEMA.get(self.section)
        return (src or {}).get(self.key)

    def works_for(self, type_id) -> bool:
        """Влияет ли параметр на результат при этом типе инициативы."""
        return not self.types or type_id in self.types


def _pc(x):
    """Долю — в проценты для подсказки: человек думает процентами, вводит долями."""
    v = x * 100
    return (f"{v:g}%").replace(".", ",")


def _frac(x):
    return (f"{x:g}").replace(".", ",")


def _num_hint(x):
    # Через calc.nf(), а не своим `.replace`: линт в тестах поймал ровно это дублирование
    # у меня же. Второй способ форматировать числа — второй способ их сломать.
    return calc.nf(x) if abs(x) >= 1000 else (f"{x:g}").replace(".", ",")


def P(key, section, label, **kw):
    return Param(key=key, section=section, label=label, **kw)


# ------------------------------------------------------------------ ТИПЫ ИНИЦИАТИВ
# ВАЖНО: движок НЕ читает `meta.type` — он ветвится только по типам каналов. Поэтому тип
# инициативы это методическая рамка (какие вопросы важны и что заполнять), а не переключатель
# формул. Отсюда и правило: параметр, помеченный как «не для этого типа», всё равно попадает
# в расчёт, если в нём стоит ненулевое значение. Прятать его нельзя — спрятанные деньги
# продолжают считаться.
INITIATIVE_TYPES = {
    "comms": {
        "label": "Коммуникация по своей базе",
        "kinds": ("internal",),
        "metric": "инкрементальный отклик против контрольной группы",
        "note": "Отклик задавать как uplift над контрольной группой, а не как абсолютный. "
                "Без контрольной группы результат недоказуем — так и писать.",
    },
    "frk4": {
        "label": "ФРК 4 — внутренние каналы и перформанс",
        "kinds": ("internal", "perform"),
        "metric": "инкрементальный CPA",
        "note": "Охватных каналов нет, поэтому органика и halo в расчёт не входят.",
    },
    "frk3": {
        "label": "ФРК 3 — + внешние посевы",
        "kinds": ("internal", "perform"),
        "metric": "окупается ли шаг с ФРК 4",
        "note": "Смотреть на предельную отдачу шага, а не на средний ROMI уровня.",
    },
    "frk2": {
        "label": "ФРК 2 — + наружная реклама",
        "kinds": ("internal", "perform", "brand"),
        "metric": "предельный NPV шага и чем доказывается охватная часть",
        "note": "Появляется brand-эффект: включаются органика и halo. Это самое недоказуемое "
                "звено модели — нужен geo-holdout или MMM.",
    },
    "frk1": {
        "label": "ФРК 1 — + телевидение",
        "kinds": ("internal", "perform", "brand"),
        "metric": "предельный NPV шага",
        "note": "То же, что ФРК 2, но ставка кратно выше. «А нужен ли ТВ» — самый дорогой "
                "вопрос в брифе.",
    },
    "product_launch": {
        "label": "Вывод продукта на полку",
        "kinds": ("internal", "perform", "brand"),
        "metric": "NPV и срок окупаемости, а НЕ ROMI",
        "note": "ROMI считается только к маркетинговым затратам и не видит IT-разработку "
                "и opex: будет выглядеть отлично при глубоко отрицательном NPV.",
    },
    "pilot": {
        "label": "Пилот или акция",
        "kinds": ("internal", "perform"),
        "metric": "достаточен ли дизайн, чтобы вообще что-то доказать",
        "note": "Для акций обязателен deadweight: «скидка дала +40%» бессмысленно, если "
                "половина взяла бы и так.",
    },
}
TYPE_CHOICES = [(k, v["label"]) for k, v in INITIATIVE_TYPES.items()]

# ------------------------------------------------------------------ КАТАЛОГ ПРОДУКТОВ
# Возрастные рамки живут в calc.PRODUCT_AGE — это правила банка. Здесь к ним добавлена
# готовая дисконтированная ценность клиента по сегментам: число из валидированной модели
# продукта, грейд A. Оно заменяет десяток допущений юнит-экономики грейдов C и D.
#
# Что ВНУТРИ этих чисел: отток, ramp-up, обслуживание, риск. Что НЕ входит: затраты
# на внешнее привлечение — их добавляет сама модель через медиабюджет и производство.
# Поэтому двойного счёта CAC не возникает.
VALUE_HORIZON_MONTHS = 36
VALUE_RATE_ANNUAL = 0.20
VALUE_EXCLUDES = "затраты на внешнее привлечение (CAC)"

PRODUCT_CATALOG = {
    # именные продукты линейки — с присланной ценностью клиента
    "kids_card":        {"label": "Детская карта",           "value": {"kids": 120}},
    "kids_deposit":     {"label": "Вклад СберKids+",         "value": {"kids": 21903}},
    "kids_key_deposit": {"label": "Вклад Детский Ключевой",  "value": {"kids": 14670, "teens": 9805}},
    "youth_deposit":    {"label": "СберВклад Молодёжный",    "value": {"teens": 513, "youth": 860}},
    "savings":          {"label": "Накопительный счёт",      "value": {"teens": 418, "youth": 795}},
    "broker":           {"label": "Брокерский счёт",         "value": {"teens": 760, "youth": 1100}},
    # родовые категории — ценности нет, считаются снизу вверх
    "card":       {"label": "Карта (родовая)"},
    "account":    {"label": "Счёт"},
    "deposit":    {"label": "Вклад (родовой)"},
    "piggy_bank": {"label": "Копилка"},
    "prime":      {"label": "Подписка Прайм"},
    "loyalty":    {"label": "Программа лояльности"},
    "credit":     {"label": "Кредит"},
    "ecosystem":  {"label": "Экосистема"},
}
PRODUCTS = [(k, v["label"]) for k, v in PRODUCT_CATALOG.items()]
PRODUCT_NAME = {k: v["label"] for k, v in PRODUCT_CATALOG.items()}


# ------------------------------------------------------------------ ОД ПО ВОЗРАСТАМ
# Операционный доход на одного клиента в ГОД, ₽, по продуктовым линиям и годам жизни.
# Свой факт из отчётности — грейд A.
#
# Две особенности исходных данных, которые здесь зафиксированы намеренно:
#   1. В исходном «итого» строка ипотеки не суммировалась ни в одном возрасте.
#      Поэтому ипотека вынесена отдельно и по умолчанию НЕ включается — иначе наши
#      цифры перестали бы биться с теми, что уже ходят по отчётам. Включается флагом.
#   2. Единственный возраст, где ОД падает, — переход 17 → 18: текущие счета −46%,
#      вклады −33%. Это уход родительских денег при переходе во взрослую линейку.
OD_AGES = tuple(range(14, 25))
OD_BY_AGE = {
    "прочее":                 (393, 569, 706, 826.6, 1279.6, 1533.6, 1686.5, 1913.85, 2030, 2261.54, 2497),
    "дебетовая карта":        (557, 677, 627, 629, 753, 913, 1082, 1247, 1575.33, 1800, 1951.65),
    "текущие счета":          (913.7, 919, 910, 949, 508, 504.8, 622, 766.54, 925, 1128.4, 1306),
    "срочные счета (вклады)": (650.7, 752, 781, 888, 592, 450, 446, 463, 487, 538, 591),
    "ипотека":                (0, 0, 0, 0, 0, 0.6, 5.5, 132, 140, 145.5, 405),
}
OD_EXCLUDED_BY_DEFAULT = ("ипотека",)

# По детским возрастам известен только средний ОД целиком, без разбивки по линиям:
# 219 ₽ в месяц на клиента. Держим ОТДЕЛЬНО от OD_BY_AGE намеренно — иначе таблица
# создавала бы видимость детализации, которой нет.
#
# Заодно эта цифра подтверждает единицу измерения основной таблицы: 219 × 12 = 2 628 ₽
# в год ложится вплотную к 2 514 ₽ у четырнадцатилетних. Значит там годовые суммы,
# а не месячные. И объясняет провал на переходе 17 → 18: у ребёнка ОД держится на
# родительских деньгах, а не на его собственных.
OD_KIDS_MONTHLY = 219
OD_FLAT_ANNUAL = {a: OD_KIDS_MONTHLY * 12 for a in range(0, 14)}


# ------------------------------------------------------------ КЛИЕНТЫ ПО ГОДАМ ЖИЗНИ
# Клиентская база банка по годам жизни, чел. Свой факт — грейд A.
# Подсегменты источника (0–5, 6–13, 14–17, 18–24) не совпадают с сегментами модели:
# первые два вместе образуют kids (0–13).
#
# Форма распределения сама по себе — сообщение: это не демография, а накопление базы
# с возрастом. В 6 лет клиентов 110 тыс., в 13 — 955 тыс., в девять раз больше.
# Банк набирает детей постепенно, и весь незанятый рынок сосредоточен в младших возрастах.
AGE_CLIENTS = {
    0: 1_000, 1: 4_000, 2: 6_500, 3: 9_000, 4: 13_000, 5: 16_500,
    6: 110_000, 7: 305_000, 8: 460_000, 9: 635_000,
    10: 760_000, 11: 855_000, 12: 920_000, 13: 955_000,
    14: 967_500, 15: 1_053_000, 16: 1_197_000, 17: 1_278_000,
    18: 1_292_700, 19: 1_292_700, 20: 1_311_300, 21: 1_404_300,
    22: 1_376_400, 23: 1_348_500, 24: 1_274_100,
}
# Размер рынка по подсегментам, чел. Разбивки по годам нет — считаем равномерной.
#
# Подсегмент 0–5 стоит отдельного внимания: 6 млн человек, а клиентов из них 50 тыс. —
# проникновение 0,8%, на порядок ниже любого другого возраста. При этом детские вклады
# доступны им с рождения. Это самый незанятый кусок рынка во всей линейке, и модель
# должна показывать его отдельно, а не растворять в среднем по сегменту «дети».
MARKET_BY_AGES = {(0, 5): 6_000_000, (6, 13): 11_000_000,
                  (14, 17): 6_000_000, (18, 24): 10_500_000}
MARKET_KNOWN_AGES = {a for lo, hi in MARKET_BY_AGES for a in range(lo, hi + 1)}


def segment_ages(segment_id):
    seg = calc.SEGMENTS[segment_id]
    return [a for a in range(seg["age_min"], seg["age_max"] + 1)]


def clients_in_segment(segment_id):
    return sum(AGE_CLIENTS.get(a, 0) for a in segment_ages(segment_id))


def market_in_segment(segment_id):
    """Рынок сегмента: сумма подсегментов рынка, попадающих в его возрастные рамки."""
    ages = set(segment_ages(segment_id))
    total = 0.0
    for (lo, hi), size in MARKET_BY_AGES.items():
        span = [a for a in range(lo, hi + 1) if a in ages]
        if span:
            total += size * len(span) / (hi - lo + 1)     # рынок равномерен по годам
    return total or None


def penetration(segment_id):
    """Доля рынка, уже являющаяся клиентами банка.

    Главное следствие для модели: при высоком проникновении «привлечение» на деле
    оказывается кросс-продажей. Модель считает охват против всей ЦА, поэтому если
    инициатива про НОВЫХ клиентов, знаменателем должен быть незанятый рынок,
    а не весь. См. предупреждение в calc.validate().
    """
    mk = market_in_segment(segment_id)
    return clients_in_segment(segment_id) / mk if mk else None


def od_per_client(segment_id, lines=None, include_mortgage=False):
    """ОД на клиента в год для сегмента, ₽ — взвешенный по числу клиентов каждого возраста.

    Взвешивание по клиентам, а не равномерное по годам: у старших возрастов и ОД выше,
    и клиентов больше, поэтому равномерное среднее занижало бы результат. На практике
    поправка мелкая (+1,0% у подростков, +0,1% у молодёжи) — распределение почти ровное.
    Но это уже свой факт, а не допущение, и грейд источника поднимается с C до A.
    """
    ages = segment_ages(segment_id)
    if lines is not None:
        # разбивка по линиям есть только в детальной таблице 14–24
        idx = {a: i for i, a in enumerate(OD_AGES)}
        have = [a for a in ages if a in idx]
        if not have:
            return None
        w = [AGE_CLIENTS.get(a, 0) or 1 for a in have]
        return (sum(sum(OD_BY_AGE[l][idx[a]] for l in lines) * wi for a, wi in zip(have, w))
                / sum(w))
    return od_for_ages(ages, include_mortgage)


def od_breakdown(segment_id, include_mortgage=False):
    """Раскладка ОД сегмента по продуктовым линиям — видно, чем именно клиент зарабатывает."""
    return {l: od_per_client(segment_id, [l], include_mortgage) for l in OD_BY_AGE
            if include_mortgage or l not in OD_EXCLUDED_BY_DEFAULT}


def product_values(product_id):
    """Ценность клиента по сегментам, если она есть в каталоге."""
    return dict(PRODUCT_CATALOG.get(product_id, {}).get("value") or {})


def product_age(product_id):
    return calc.PRODUCT_AGE.get(product_id, (0, None))


def product_segments(product_id):
    """Сегменты, которым продукт доступен по возрасту."""
    return [sid for sid in calc.SEGMENTS
            if calc.segment_availability(sid, product_id)[0] > 0]


def overlap_ages(segment_id, product_id):
    """Годы жизни, где сегмент и возрастная вилка продукта пересекаются."""
    seg = calc.SEGMENTS[segment_id]
    lo, hi = product_age(product_id)
    top = seg["age_max"] if hi is None else min(seg["age_max"], hi)
    return list(range(max(seg["age_min"], lo), top + 1))


def market_for_ages(ages):
    """Рынок для набора возрастов. Внутри подсегмента рынок считаем равномерным по годам."""
    ages = set(ages)
    total = 0.0
    for (lo, hi), size in MARKET_BY_AGES.items():
        span = [a for a in range(lo, hi + 1) if a in ages]
        if span:
            total += size * len(span) / (hi - lo + 1)
    return total or None


def od_for_ages(ages, include_mortgage=False):
    """ОД на клиента в год для набора возрастов, взвешенный по числу клиентов.

    Два источника разной точности: по 14–24 есть разбивка по продуктовым линиям,
    по младшим возрастам — только средняя сумма. Берём что есть, приоритет у детального.
    """
    idx = {a: i for i, a in enumerate(OD_AGES)}
    lines = [k for k in OD_BY_AGE if include_mortgage or k not in OD_EXCLUDED_BY_DEFAULT]
    vals = {}
    for a in ages:
        if a in idx:
            vals[a] = sum(OD_BY_AGE[l][idx[a]] for l in lines)
        elif a in OD_FLAT_ANNUAL:
            vals[a] = OD_FLAT_ANNUAL[a]
    if not vals:
        return None
    w = {a: AGE_CLIENTS.get(a, 0) or 1 for a in vals}
    return sum(vals[a] * w[a] for a in vals) / sum(w.values())


def segment_defaults(segment_id, product_id):
    """Всё, что известно про сегмент и продукт ДО ввода пользователя.

    Ключевая тонкость — что считать размером сегмента. Модель понимает `target_size`
    как ЦА, которую потом режет `reachable_share`. Если подставить рынок всего сегмента
    (0–13) и оставить долю по возрастам, получится одна отсечка; если подставить рынок
    доступных возрастов (6–13) и не поправить долю — отсечка применится второй раз,
    и сегмент похудеет вдвое. Так и произошло на первом живом тесте.

    Поэтому здесь одно согласованное решение: `target_size` — это рынок ИМЕННО тех
    возрастов, где продукт доступен, а `reachable_share` = 1. Возрастное правило уже
    учтено в числителе, повторно его применять не надо.
    """
    ages = overlap_ages(segment_id, product_id)
    if not ages:
        return None
    market = market_for_ages(ages)
    clients = sum(AGE_CLIENTS.get(a, 0) for a in ages)
    blind = sorted(set(ages) - MARKET_KNOWN_AGES)
    return {
        "ages": (ages[0], ages[-1]),
        "target_size": market,
        "existing_clients": clients or None,
        "penetration": (clients / market) if market and clients else None,
        # возрасты, где клиенты есть, а рынок неизвестен: проникновение слегка завышено
        "market_partial": blind or None,
        "reachable_share": 1.0,
        "value_per_client": product_values(product_id).get(segment_id),
        "operating_income_annual": od_for_ages(ages),
    }


def product_economics(product_id):
    """Режим экономики и горизонт, вытекающие из каталога продукта."""
    if not product_values(product_id):
        return {"mode": calc.MODE_BUILD_UP}
    return {"mode": calc.MODE_VALUE,
            "value_horizon_months": VALUE_HORIZON_MONTHS,
            "discount_rate_annual": VALUE_RATE_ANNUAL,
            "horizon_months": VALUE_HORIZON_MONTHS}

BRAND_TYPES = tuple(k for k, v in INITIATIVE_TYPES.items() if "brand" in v["kinds"])
FRK_TYPES = ("frk4", "frk3", "frk2", "frk1")


# ------------------------------------------------------------------ МЕТА
META = [
    P("name", "meta", "Название инициативы", fmt="text", default="", grade="A",
      note="Свободный текст. Попадает в отчёт и в лист «Итог» книги."),
    P("type", "meta", "Тип инициативы", fmt="text", default="frk4", grade="A",
      note="comms · frk1–frk4 · product_launch · pilot. Определяет, какие вопросы важны."),
    P("product", "meta", "Продукт", fmt="text", default="card", grade="A",
      note="От продукта зависят возрастные правила: карта с 6 лет, брокерский счёт с 14, кредит с 18."),
    P("horizon_months", "meta", "Горизонт оценки", unit="мес", fmt="int", default=24, grade="A",
      note="На сколько месяцев считаем эффект. Не меньше длительности кампании, иначе часть "
           "бюджета и притока молча теряется."),
    P("discount_rate_annual", "meta", "Ставка дисконтирования (годовая)", unit="%", fmt="pct",
      default=0.20, grade="A",
      note="Стоимость капитала. Месячная = (1+годовая)^(1/12)−1."),
    P("currency", "meta", "Валюта расчёта", fmt="text", default="RUB", grade="A",
      note="Код валюты, в которой заданы все суммы. Проект в другой стране считать "
           "в местной валюте: пересчёт каждого допущения в уме — источник ошибок на порядок."),
    P("fx_to_rub", "meta", "Курс к рублю", unit="ед. валюты за 1 ₽", fmt="num", default=0,
      grade="C",
      note="Нужен только при валюте, отличной от рубля: ключевые суммы дублируются в рублях "
           "для комитета. Ноль — не пересчитывать."),
]

# ------------------------------------------------------------------ АУДИТОРИЯ
AUDIENCE = [
    P("target_size", "audience", "Размер целевой аудитории", unit="чел", fmt="int",
      default=1_000_000, key_assumption=True, segment_scoped=True,
      note="Знаменатель охвата и частоты. Не вся РФ, а именно ЦА продукта. "
           "При выборе сегментов задаётся по каждому отдельно."),
    P("baseline_organic_apps_m", "audience", "Органические заявки в месяц", unit="шт/мес",
      fmt="int", default=5_000, grade="A", key_assumption=True, segment_scoped=True,
      types=BRAND_TYPES,
      note="Текущий поток БЕЗ кампании. База, к которой применяется brand-uplift. "
           "Если здесь ноль, охватные каналы дадут ровно ноль заявок."),
    P("halo_months", "audience", "Halo-период после кампании", unit="мес", fmt="num", default=2,
      types=BRAND_TYPES,
      note="Сколько месяцев brand-эффект тянется после открутки. Без охватных каналов "
           "не влияет ни на что."),
    P("halo_decay", "audience", "Затухание halo", unit="доля", fmt="pct", default=0.5,
      types=BRAND_TYPES,
      note="Какая доля эффекта сохраняется в halo-период."),
    P("reachable_market", "audience", "Достижимый рынок", unit="чел", fmt="int", default=0,
      grade="B", key_assumption=True,
      note="Знаменатель доли рынка: сколько человек продукт вообще может получить. "
           "Не весь рынок: если карта работает только у владельцев смартфона, "
           "остальные не купят её ни при каком бюджете. Ноль — не считать долю."),
    P("market_total", "audience", "Рынок целиком", unit="чел", fmt="int", default=0, grade="C",
      note="Справочно, рядом с достижимым. Доля от всего рынка всегда красивее и всегда "
           "означает меньше — движок показывает обе и напоминает, какая из них рабочая."),
]

# ------------------------------------------------------------------ ПЛАН ПРОДАЖ
ACQUISITION = [
    P("year1_target", "acquisition", "План продаж на первый год", unit="чел", fmt="int",
      default=0, grade="A", key_assumption=True,
      note="Если задан, объём берётся из плана, а не из медиаворонки — так устроен вывод "
           "продукта на полку. Медиаворонка при этом не выбрасывается: движок показывает "
           "разрыв между тем, что даёт бюджет, и тем, что обещает план. Ноль — считать по воронке."),
    P("yoy_growth", "acquisition", "Темп год к году", unit="×", fmt="num", default=1.0, grade="C",
      note="Множитель плана следующего года к предыдущему. 0,8 значит, что во второй год "
           "приток на 20% ниже: рынок вычерпывается, лёгкие клиенты кончаются первыми."),
]

# ------------------------------------------------- РАСТРАНЗАЧИВАНИЕ СВОЕЙ БАЗЫ
# Третья сущность эффекта: тот же человек, тот же продукт, другое поведение.
# На зрелом рынке (проникновение 89% у молодёжи) это основной эффект инициативы:
# новых людей взять неоткуда, весь прирост — в том, что спящий клиент просыпается.
BASE_ACTIVATION = [
    P("name", "base_activation", "Название механики", fmt="text",
      default="Растранзачивание базы", grade="A",
      note="Свободный текст для отчёта."),
    P("eligible_clients", "base_activation", "Малоактивных клиентов в ЦА", unit="чел",
      fmt="int", default=0, grade="A", key_assumption=True,
      note="Сколько своих клиентов подпадают под определение спящих. Своя выгрузка — "
           "грейд A. Ноль отключает механику целиком."),
    P("workout_months", "base_activation", "За сколько прорабатывается база", unit="мес",
      fmt="num", default=0, grade="B", key_assumption=True,
      note="Накопленная база КОНЕЧНА: её проработали и она кончилась. Ноль — растянуть "
           "по форме кампании. Складывать сток с потоком в одну строку «эффект в месяц» "
           "нельзя: на живом ФЭО базы в 1,97 млн при заявленном темпе хватило бы "
           "на 892 месяца."),
    P("monthly_inflow", "base_activation", "Пополнение базы в месяц", unit="чел/мес",
      fmt="int", default=0, grade="A", key_assumption=True,
      note="ПОТОК: сколько клиентов попадает в это состояние каждый месяц. В отличие "
           "от стока работает всегда, и на горизонте обычно даёт больше. Обосновывать "
           "инициативу надо потоком, хотя сток выглядит внушительнее."),
    P("technically_reachable", "base_activation", "Технически достижимы", unit="%",
      fmt="pct", default=1.0, grade="A",
      note="Есть приложение и не в стоп-листе — физическая возможность доставить "
           "сообщение. Это НЕ конверсия: слитые в один множитель, они прячут, где "
           "теряется база. В живом ФЭО 70% были достижимостью, а не откликом, "
           "и лечатся эти потери совершенно по-разному."),
    P("reach_share", "base_activation", "До скольких дотянулись", unit="%", fmt="pct",
      default=0.3, grade="B",
      note="Доля ДОСТИЖИМЫХ, которых реально коснулась коммуникация. Не путать "
           "с охватом рынка: здесь знаменатель — своя спящая база."),
    P("uplift_rate", "base_activation", "Доля сменивших поведение", unit="%", fmt="pct",
      default=0.1, grade="A", key_assumption=True,
      note="ТОЛЬКО прирост над контрольной группой. Абсолютная доля активировавшихся "
           "включает тех, кто проснулся бы и сам — от сезона, зарплаты, чужих активностей."),
    P("measured_against_control", "base_activation", "Замерено против контроля",
      fmt="text", default="", grade="A",
      note="Непусто, если uplift получен сравнением с контрольной группой. Пусто — "
           "движок понижает грейд блока до C и пишет об этом: спящая база оживает и сама."),
    P("turnover_before", "base_activation", "Оборот до, в месяц", unit="₽/мес", fmt="money",
      default=0, grade="A",
      note="Средний месячный оборот малоактивного клиента до инициативы."),
    P("turnover_after", "base_activation", "Оборот после, в месяц", unit="₽/мес", fmt="money",
      default=0, grade="B", key_assumption=True,
      note="Тот же клиент после. В деньги идёт РАЗНИЦА, а не сам оборот: часть трат "
           "была и раньше."),
    P("balance_before", "base_activation", "Остаток до", unit="₽", fmt="money", default=0,
      grade="A", note="Средний остаток малоактивного клиента до инициативы."),
    P("balance_after", "base_activation", "Остаток после", unit="₽", fmt="money", default=0,
      grade="B", note="Тот же клиент после. В деньги идёт разница."),
    P("decay_months", "base_activation", "Затухание эффекта", unit="мес", fmt="num",
      default=12, grade="C", key_assumption=True,
      note="За сколько месяцев всплеск активности гаснет до нуля. Ноль означает "
           "вечный эффект — так почти не бывает, и движок об этом предупредит."),
    P("attribution_share", "base_activation", "Доля, приписанная инициативе", unit="%",
      fmt="pct", default=1.0, grade="B",
      note="При честном замере против контроля здесь законна единица: uplift над "
           "контролем инкрементален по построению. Без контроля — режьте."),
]

# ------------------------------------------------------------------ ВОРОНКА
FUNNEL = [
    P("approval_rate", "funnel", "Approval rate", unit="%", fmt="pct", default=0.60,
      typical=(0.2, 0.95),
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля одобренных заявок. Карты 50–70%, кредиты 20–40%, вклады ~95%."),
    P("activation_rate", "funnel", "Activation rate", unit="%", fmt="pct", default=0.65,
      typical=(0.55, 0.75),
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля одобренных, дошедших до целевого действия. Самая недооценённая дыра воронки."),
    P("cannibalization", "funnel", "Каннибализация", unit="%", fmt="pct", default=0.15,
      typical=(0.1, 0.25),
      key_assumption=True, segment_scoped=True,
      note="Доля клиентов, переехавших со своих же продуктов. Ноль ставить только с обоснованием: "
           "в банке новый продукт почти всегда ест старый."),
    P("deadweight", "funnel", "Deadweight (взяли бы и так)", unit="%", fmt="pct", default=0.0,
      typical=(0.3, 0.6),
      grade="D", segment_scoped=True, types=("pilot",), key_assumption=True,
      note="Доля тех, кто оформил бы и без промо. Для акций 30–60%, для обычных кампаний 0. "
           "«Скидка дала +40%» бессмысленно, если половина взяла бы и так."),
]

# ------------------------------------------------------------------ ЮНИТ-ЭКОНОМИКА
UNIT = [
    P("avg_balance", "unit_economics", "Средний баланс", unit="₽", fmt="money", default=45_000,
      typical=(30_000, 60_000),
      grade="A", key_assumption=True, segment_scoped=True,
      note="Остаток на счёте. Источник процентной маржи."),
    P("nim_annual", "unit_economics", "NIM / FTP-спред (годовой)", unit="%", fmt="pct",
      typical=(0.05, 0.08),
      default=0.07, grade="A", segment_scoped=True,
      note="Ставка, по которой банк зарабатывает на остатке."),
    P("monthly_turnover", "unit_economics", "Оборот по карте в месяц", unit="₽", fmt="money",
      default=28_000, grade="A", segment_scoped=True,
      note="База для интерчейнджа и кэшбэка."),
    P("interchange_rate", "unit_economics", "Ставка интерчейнджа", unit="%", fmt="pct2",
      typical=(0.008, 0.014),
      default=0.011, grade="A", segment_scoped=True, note="Комиссия от оборота, обычно 0,8–1,4%."),
    P("fee_income_m", "unit_economics", "Прочий комиссионный доход", unit="₽/мес", fmt="money",
      default=0, segment_scoped=True, note="Подписка, СМС-информирование, страховки."),
    P("cashback_rate", "unit_economics", "Кэшбэк (доля оборота)", unit="%", fmt="pct2",
      typical=(0.004, 0.008),
      default=0.005, grade="A", segment_scoped=True, note="Программа лояльности. Прямой вычет из маржи."),
    P("servicing_cost_m", "unit_economics", "Стоимость обслуживания", unit="₽/мес", fmt="money",
      typical=(50, 100),
      default=70, grade="B", segment_scoped=True,
      note="Себестоимость поддержки: процессинг, саппорт, выпуск."),
    P("cor_annual", "unit_economics", "Cost of Risk (годовой)", unit="%", fmt="pct", default=0.0,
      grade="A", key_assumption=True, segment_scoped=True,
      note="ОБЯЗАТЕЛЕН для кредитных продуктов. Для дебетовых 0. Без него кредитка всегда "
           "выглядит прекрасно и всегда врёт."),
    P("ramp_months", "unit_economics", "Ramp-up маржи", unit="мес", fmt="num", default=3,
      grade="B", segment_scoped=True,
      note="За сколько месяцев клиент выходит на полную маржу."),
    P("monthly_churn", "unit_economics", "Месячный отток", unit="%", fmt="pct", default=0.035,
      typical=(0.02, 0.05),
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля активных, отваливающихся за месяц. Определяет LTV. Ноль означает вечного клиента "
           "и завышает NPV."),
    P("balance_to_turnover", "unit_economics", "Остаток как доля от оборота", unit="доля",
      fmt="pct", default=0, grade="B",
      note="Если задан, средний баланс считается от месячного оборота и перекрывает "
           "введённый вручную. Остаток на карте — это доля прошедших через неё денег, "
           "а не свободное число: заданные порознь, они легко противоречат друг другу, "
           "и модель считает без ошибок. Ноль — задавать баланс напрямую."),
    P("mode", "unit_economics", "Режим экономики клиента", fmt="text", default="build_up",
      grade="A",
      note="build_up — собрать снизу вверх из строк выше. value_per_client — взять готовую "
           "дисконтированную ценность клиента одним числом; тогда строки выше в расчёте "
           "НЕ участвуют, а срок окупаемости не считается."),
    P("value_per_client", "unit_economics", "Ценность клиента (готовая)", unit="₽", fmt="money",
      default=0, grade="A", key_assumption=True, segment_scoped=True,
      note="Дисконтированная ценность одного клиента за свой горизонт, БЕЗ затрат на привлечение. "
           "Отток, ramp-up, обслуживание и риск уже внутри неё — поверх они не применяются."),
    P("value_horizon_months", "unit_economics", "Горизонт ценности", unit="мес", fmt="int",
      default=VALUE_HORIZON_MONTHS, grade="A",
      note="За сколько месяцев посчитана ценность. Обязан совпадать с горизонтом оценки, "
           "иначе затраты и доходы считаются на разные сроки."),
    P("operating_income_annual", "unit_economics", "ОД на клиента в год", unit="₽/год",
      fmt="money", default=0, grade="A", segment_scoped=True,
      note="Операционный доход на одного клиента за год. Это ДОХОД, а не маржа: кэшбэк, "
           "обслуживание и риск он не вычитает, поэтому с NPV напрямую несопоставим. "
           "Нужен, чтобы показать эффект в ОД — валюте, в которой считают финансы."),
]

# ------------------------------------------------------------------ ЗАТРАТЫ
COSTS = [
    P("campaign_months", "costs", "Длительность кампании", unit="мес", fmt="int", default=3,
      grade="A", note="За этот период тратится медиабюджет и приходят клиенты (равномерно)."),
    P("production", "costs", "Производство и креатив", unit="₽", fmt="money", default=0,
      grade="A", note="Разовые, списываются в 1-й месяц. Входят в маркетинговые затраты для ROMI."),
    P("it_dev", "costs", "IT-разработка", unit="₽", fmt="money", default=0, grade="A",
      types=("product_launch",),
      note="Для вывода продукта на полку. В ROMI НЕ входит, в NPV — да."),
    P("one_off_other", "costs", "Прочие разовые затраты", unit="₽", fmt="money", default=0,
      grade="A", types=("product_launch", "pilot"),
      note="Юристы, комплаенс, лицензии, интеграции."),
    P("opex_monthly", "costs", "Постоянные расходы", unit="₽/мес", fmt="money", default=0,
      grade="A", types=("product_launch",),
      note="Run-rate: поддержка продукта, команда. Списывается каждый месяц горизонта."),
    P("acq_cost_per_client", "costs", "Выплата за привлечение", unit="₽/клиента", fmt="money",
      default=0, grade="A", segment_scoped=True, types=("comms", "pilot", "product_launch"),
      note="Реферальные выплаты, промокоды, бонус за открытие. Платится за КАЖДОГО сработавшего, "
           "не за инкремент: за deadweight и каннибализацию вы платите, ничего не получая."),
]

# ------------------------------------------------------------------ RICE
RICE = [
    P("reach", "rice", "Reach", unit="чел", fmt="int", default=0, grade="B",
      note="Ноль — взять инкремент базового сценария."),
    P("impact", "rice", "Impact", unit="0.25–3", fmt="num", default=2, grade="D",
      note="Насколько сильно влияет на одного затронутого клиента."),
    P("confidence", "rice", "Confidence", unit="%", fmt="pct", default=0.6, grade="D",
      note="Уверенность в оценке. Синхронизируйте с индексом качества данных."),
    P("effort", "rice", "Effort", unit="чел-мес", fmt="num", default=12, grade="B",
      note="Трудозатраты всех команд. Знаменатель RICE."),
]

RICE.append(
    P("hurdle_ltv_cac", "rice", "Норма LTV / CAC", unit="×", fmt="num", default=3.0, grade="A",
      note="Порог, ниже которого масштабирование увеличивает убыток. От него движок "
           "считает предельный CPA и предельную цену контакта: сколько вообще МОЖНО "
           "платить, а не сколько платим. Это ответ на вопрос «на какой бюджет»."))

# ------------------------------------------------------------------ КАЧЕСТВО ДАННЫХ
GRADES = [
    P("A", "confidence", "Грейд A — свой факт из BI", unit="шт", fmt="int", default=0,
      note="Вес 1,00. Прошлые кампании этого же продукта, выгрузка из DWH."),
    P("B", "confidence", "Грейд B — свой аналог", unit="шт", fmt="int", default=0,
      note="Вес 0,75. Другой продукт, другая ЦА или период, но свои данные."),
    P("B_minus", "confidence", "Грейд B− — перенос с другого рынка", unit="шт", fmt="int",
      default=0,
      note="Вес 0,60. Цифра из валидированной модели, но не этой страны или не этой географии. "
           "Не свой факт и не свой аналог, но и не бенчмарк. При выходе на новый рынок такими "
           "оказываются почти все параметры экономики сразу — и выглядят обоснованными, "
           "потому что взяты из работающей модели. Проверять локальными данными в первую "
           "очередь их, а не догадки."),
    P("C", "confidence", "Грейд C — рыночный бенчмарк", unit="шт", fmt="int", default=0,
      note="Вес 0,50. Публичная отчётность, отраслевое исследование."),
    P("D", "confidence", "Грейд D — догадка", unit="шт", fmt="int", default=0,
      note="Вес 0,20. Чем больше таких, тем ниже индекс и тем скорее вердикт станет «сначала пилот»."),
]

# ------------------------------------------------------------------ КАНАЛЫ
# Каналы — список, а не раздел: у инициативы их произвольное число.
CHANNELS = [
    P("name", "channels", "Канал", fmt="text", default=""),
    P("kind", "channels", "Тип", fmt="text", default="perform",
      note="internal — свои каналы (push, in-app, SMS); perform — прямой отклик; "
           "brand — охватные (ТВ, наружка), работают только через прирост органики."),
    P("frk_tier", "channels", "ФРК", fmt="int", default=4,
      note="Внутренние и перформанс = 4, внешние посевы = 3, наружная = 2, ТВ = 1. "
           "Уровни вложены: 4 ⊂ 3 ⊂ 2 ⊂ 1.", kinds=("internal", "perform", "brand")),
    P("budget", "channels", "Бюджет", unit="₽", fmt="money", default=0),
    P("cpm", "channels", "CPM", unit="₽", fmt="money", default=0,
      typical=(100, 500),
      note="Показы = бюджет / CPM × 1000.", kinds=("perform", "brand")),
    P("ctr", "channels", "CTR", unit="%", fmt="pct2", default=0,
      typical=(0.003, 0.015), note="Для perform-каналов.", kinds=("perform",)),
    P("response_rate", "channels", "Response rate", unit="%", fmt="pct2", default=0,
      typical=(0.01, 0.05),
      note="Для internal-каналов. Для коммуникаций задавать как uplift над контрольной группой.", kinds=("internal",)),
    P("cr_to_app", "channels", "CR в заявку", unit="%", fmt="pct", default=0,
      typical=(0.02, 0.08), kinds=("internal", "perform")),
    P("brand_uplift", "channels", "Brand uplift", unit="%", fmt="pct", default=0,
      typical=(0.03, 0.15),
      note="Для охватных каналов: на сколько процентов растёт органика.", kinds=("brand",)),
    P("reach_abs", "channels", "Охват базы", unit="чел", fmt="int", default=0,
      note="Для internal-каналов вместо показов.", kinds=("internal",)),
    P("impressions", "channels", "Показы", unit="шт", fmt="int", default=0,
      note="Если CPM не задан — показы напрямую.", kinds=("perform", "brand")),
    P("effective_freq_target", "channels", "Эффективная частота", unit="контактов", fmt="num",
      default=3, note="Полный brand-эффект достигается при этой частоте контакта.", kinds=("brand",)),
]

SECTIONS = [
    ("meta", "Инициатива", META),
    ("audience", "Аудитория", AUDIENCE),
    ("acquisition", "План продаж", ACQUISITION),
    ("base_activation", "Растранзачивание своей базы", BASE_ACTIVATION),
    ("funnel", "Воронка", FUNNEL),
    ("unit_economics", "Юнит-экономика клиента", UNIT),
    ("costs", "Затраты", COSTS),
    ("rice", "RICE — для сравнения инициатив между собой", RICE),
    ("confidence", "Качество данных — грейд источника каждого допущения", GRADES),
]

# ------------------------------------------------------------------ ВЛОЖЕННЫЕ БЛОКИ
# Форма из SECTIONS рендерит скалярные поля: одно поле — одно число или строка.
# Эти блоки скалярами не описываются — внутри объекты и списки, — поэтому в SECTIONS
# их нет и веб-форма их пока не показывает (BACKLOG 20).
#
# Но выпасть из-под проверки они не должны: _assert_consistent() сверяет их состав
# с собственными словарями движка. Иначе новый вложенный блок появился бы в движке
# и молча остался неизвестным всему остальному проекту — ровно то расхождение,
# ради предотвращения которого этот файл и существует.
NESTED_KEYS = {
    ("base_activation", "parts"):
        "Части неоднородной базы: {name, share, uplift_cap}. Спящий месяц и спящий год "
        "откликаются по-разному, и в глубокой части эффект близок к нулю.",
    ("unit_economics", "turnover_split"):
        "Разложение оборота по типам операций: {тип: {share, fee}}, доли в сумме 1,0. "
        "У POS, e-POS и снятия разные ставки, и одна усреднённая их прячет.",
    ("acquisition", "seasonality"):
        "Сезонный профиль плана: 12 множителей по месяцам. Форма притока влияет на NPV — "
        "ранние когорты живут дольше и дисконтируются меньше.",
}
# `selection_haircut` живёт внутри linked_client — вложенного блока, поэтому в форму
# не попадает и здесь не описывается. См. п. 20 бэклога.
NESTED_SECTIONS = {
    "brief": ("Разбор брифа: утверждения из описания и их судьба", calc.BRIEF_KEYS),
    "pilot": ("Прошедший пилот как источник данных", calc.PILOT_KEYS),
    "linked_client": ("Связанный клиент: кто платит вместо носителя продукта", calc.LINKED_KEYS),
    "companion": ("Продукт-спутник на той же базе", calc.COMPANION_KEYS),
    "partner": ("P&L партнёра на той же клиентской базе", calc.PARTNER_KEYS),
    "natural_effect": ("Натуральный эффект и его перевод в деньги банка",
                       calc.NATURAL_KEYS),
}

ALL = [p for _, _, ps in SECTIONS for p in ps]
BY_KEY = {(p.section, p.key): p for p in ALL}
CHANNEL_BY_KEY = {p.key: p for p in CHANNELS}
SEGMENT_SCOPED = [p for p in ALL if p.segment_scoped]


def split_by_type(ps, type_id, cfg=None):
    """Делит параметры раздела на «работают при этом типе» и «остальные».

    Параметр из второй группы с НЕнулевым значением возвращается в первой: он всё равно
    попадает в расчёт, и прятать его нельзя. Спрятанные деньги продолжают считаться —
    это ровно тот класс ошибок, ради которого писалась строгая валидация.
    """
    main, rest = [], []
    for p in ps:
        used = p.works_for(type_id)
        if not used and cfg:
            val = (cfg.get(p.section) or {}).get(p.key)
            used = bool(val) and val != p.default
        (main if used else rest).append(p)
    return main, rest


def channel_fields(kind):
    """Поля канала, которые движок читает при этом типе канала."""
    return [p for p in CHANNELS if not p.kinds or kind in p.kinds]


# Без этих полей канал не даёт ни одной заявки. Формула не ломается — она честно
# умножает на ноль, и человек получает правдоподобные нули вместо ошибки. Ровно так
# на живом тесте потерялся весь расчёт: у internal-канала не заполнили response_rate.
CHANNEL_REQUIRED = {
    "internal": ("reach_abs", "response_rate", "cr_to_app"),
    "perform": ("budget", "cpm", "ctr", "cr_to_app"),
    "brand": ("budget", "cpm", "brand_uplift"),
}


def channel_missing(ch):
    """Обязательные поля канала, которые не заполнены. Пустой список — канал сработает."""
    kind = ch.get("kind") or "perform"
    out = []
    for k in CHANNEL_REQUIRED.get(kind, ()):
        if ch.get(k):
            continue
        # показы можно задать напрямую вместо связки «бюджет + CPM»
        if k in ("budget", "cpm") and ch.get("impressions"):
            continue
        out.append(k)
    return out


def channels_ready(cfg):
    """Есть ли хотя бы один канал, который реально даст заявки."""
    return any(not channel_missing(ch) for ch in (cfg.get("channels") or []))


# ------------------------------------------------------ ПОЛЯ ВЛОЖЕННЫХ РАЗДЕЛОВ
# У вложенных разделов в движке есть только диапазоны — ни подписей, ни пояснений.
# Пока их не было, каждый блок приходилось верстать в шаблоне руками; так сделаны
# бриф и пилот, и повторять это ещё четыре раза значило бы четыре раза продублировать
# смысл параметров рядом с их же диапазонами.
#
# Здесь метаданные для ОБЩЕГО рендера: `nested_fields(section)` отдаёт список Param,
# и форма собирается тем же макросом, что и скалярные разделы.
NESTED_FIELD_META = {
    "linked_client": {
        "name": dict(label="Кто это", fmt="text",
                     note="Например: «родитель ребёнка с детской картой»."),
        "clients_per_linked": dict(
            label="Клиентов продукта на одного связанного", fmt="num", default=1.0,
            typical=(1.0, 2.0),
            note="Двое детей у одного родителя — 2. Делитель: связанных меньше, "
                 "чем носителей продукта."),
        "income_annual": dict(
            label="ОД связанного клиента в год", unit="₽/год", fmt="money",
            key_assumption=True,
            note="Операционный доход того, кто платит. Не носителя продукта."),
        "margin_rate": dict(
            label="Доля маржи в ОД", unit="%", fmt="pct", default=1.0, typical=(0.2, 0.5),
            note="ОД — это доход. В деньги банка он превращается только частью."),
        "attribution_share": dict(
            label="Доля привлечения, которую относим на инициативу", unit="%", fmt="pct",
            default=1.0, typical=(0.2, 0.6), key_assumption=True,
            note="Часть связанных пришла бы и без неё. Единица означает, что вся "
                 "заслуга наша, — это допущение, а не его отсутствие."),
        "churn_annual": dict(
            label="Годовой отток связанных", unit="%", fmt="pct", typical=(0.1, 0.3),
            note="От него считается, скольких удалось удержать."),
        "churn_reduction": dict(
            label="На сколько инициатива снижает отток", unit="%", fmt="pct",
            typical=(0.05, 0.3), key_assumption=True,
            note="Доказывается только контрольной группой по связанным. "
                 "Удержание откладывает уход, а не отменяет: спасённый оттекает дальше "
                 "по обычной ставке."),
        "selection_haircut": dict(
            label="Поправка на отбор удержанных", unit="%", fmt="pct", default=1.0,
            typical=(0.5, 0.8),
            note="Удержанный отобран по признаку «собирался уйти», то есть вовлечён "
                 "хуже среднего. Применять к нему средний ОД значит завышать."),
        "cross_sell_uplift": dict(
            label="Прирост ОД от кросс-селла", unit="%", fmt="pct", typical=(0.02, 0.15),
            note="Прирост дохода действующих связанных от того, что банк узнал о них "
                 "больше. Инкрементален по определению — это именно прирост."),
        "sources": dict(label="Источники связанных", fmt="text", config_only=True,
                        note="Доли new / reactivated / existing. Задаётся конфигом."),
    },

    "companion": {
        "name": dict(label="Что за спутник", fmt="text",
                     note="Например: «копилка поверх детской карты»."),
        "attach_rate": dict(
            label="Доля клиентов, подключивших спутник", unit="%", fmt="pct",
            typical=(0.1, 0.4), key_assumption=True,
            note="От активной базы основного продукта. Это воронка спутника, "
                 "а не отдельная инициатива."),
        "funded_rate": dict(
            label="Доля подключивших, кто реально положил деньги", unit="%", fmt="pct",
            default=1.0, typical=(0.4, 0.8),
            note="Открыть копилку и пополнить её — разные события. Единица означает, "
                 "что пополняют все, а это почти никогда не так."),
        "avg_balance": dict(
            label="Средний остаток на спутнике", unit="₽", fmt="money",
            typical=(3_000, 30_000), key_assumption=True,
            note="Источник процентной маржи спутника."),
        "nim_annual": dict(
            label="Ставка размещения остатка", unit="%", fmt="pct", typical=(0.04, 0.09),
            note="По ней остаток спутника превращается в маржу банка."),
        "balance_growth_m": dict(
            label="Рост остатка в месяц", unit="%", fmt="pct", typical=(0.0, 0.05),
            note="Копилка накапливается. Растёт как сложный процент от месяца к месяцу — "
                 "на длинном горизонте это заметно."),
        "spend_share": dict(
            label="Доля остатка, которую тратят", unit="%", fmt="pct", typical=(0.0, 0.3),
            note="Потраченное уходит с остатка. Если возвращается в оборот основного "
                 "продукта — приносит комиссию ещё раз, см. поле ниже."),
        "spend_returns_to_turnover": dict(
            label="Траты возвращаются в оборот основного продукта", fmt="text",
            note="1 — да, пусто — нет. Тогда с этих трат банк получает комиссию "
                 "второй раз. Ставить только если тратят своей же картой."),
    },

    "partner": {
        "name": dict(label="Партнёр", fmt="text",
                     note="Чей P&L считаем отдельно от банковского."),
        "fee_per_card": dict(
            label="Доход партнёра с карты", unit="₽/карта", fmt="money",
            key_assumption=True,
            note="Считается на ту же базу, но по своей экономике. Отдельным блоком, "
                 "а не слитым в общий итог: партнёру может быть выгодно то, что банку нет."),
        "cost_per_card": dict(
            label="Затраты партнёра на карту", unit="₽/карта", fmt="money",
            note="Если у партнёра минус, договориться будет не о чем — и лучше знать "
                 "это до переговоров, а не после."),
        "turnover_split": dict(
            label="Разложение оборота партнёра", fmt="text", config_only=True,
            note="Доли и ставки по типам операций. Задаётся конфигом."),
    },

    "natural_effect": {
        "name": dict(label="Что за эффект", fmt="text",
                     note="Например: «снижение просроченной задолженности»."),
        "unit": dict(label="Единица измерения", fmt="text",
                     note="₽ долга, обращения, часы. Именно натуральная единица, "
                          "а не деньги банка."),
        "baseline": dict(
            label="Накопленный объём (сток)", fmt="money", key_assumption=True,
            note="То, что уже накоплено. Сток КОНЕЧЕН: прорабатывается за срок ниже "
                 "и кончается. Путать его с потоком — самый частый способ завысить "
                 "эффект в разы."),
        "monthly_inflow": dict(
            label="Приток в месяц", fmt="money",
            note="Новое, что появляется каждый месяц. В отличие от стока не кончается."),
        "workout_months": dict(
            label="За сколько месяцев прорабатывается сток", unit="мес", fmt="num",
            typical=(3, 12),
            note="Без него накопленной базы хватило бы на сотни месяцев — на живом "
                 "расчёте выходило 892."),
        "uplift_rate": dict(
            label="Uplift над контролем", unit="%", fmt="pct", typical=(0.02, 0.15),
            key_assumption=True,
            note="Именно НАД контрольной группой, а не абсолютный результат. Часть "
                 "эффекта происходит сама: долг возвращают и без агента."),
        "measured_against_control": dict(
            label="Замерено против контрольной группы", fmt="text",
            note="1 — да, пусто — нет. Без контроля весь uplift это допущение."),
        "monetization": dict(
            label="Сколько денег банка даёт единица эффекта", fmt="num", default=1.0,
            typical=(0.1, 0.5), key_assumption=True,
            note="Рубль сниженного долга — не рубль прибыли: это высвобожденный резерв "
                 "минус недополученный процент. На живом расчёте перевод уменьшил эффект "
                 "в восемь раз."),
        "relapse_share": dict(
            label="Доля возврата эффекта", unit="%", fmt="pct", typical=(0.1, 0.4),
            note="Часть тех, кому помогли, вернётся в просрочку. Эффект не вечен."),
        "parts": dict(label="Части базы", fmt="text", config_only=True,
                      note="Доли и потолки uplift по корзинам. Задаётся конфигом: "
                           "в глубоких корзинах эффект структурно невозможен."),
        "adjustments": dict(label="Именованные вычеты", fmt="text", config_only=True,
                            note="Задаётся конфигом. Инструмент помощи почти всегда "
                                 "чего-то стоит."),
    },
}


def nested_fields(section):
    """Поля вложенного раздела как список Param — для того же макроса, что и скалярные."""
    meta = NESTED_FIELD_META.get(section) or {}
    title, keys = NESTED_SECTIONS.get(section, ("", {}))
    out = []
    for key in keys:
        m = dict(meta.get(key) or {})
        out.append(Param(key=key, section=section,
                         label=m.pop("label", key), **m))
    return out


def nested_title(section):
    return NESTED_SECTIONS.get(section, ("", {}))[0]


def defaults():
    """Конфиг из одних дефолтов — заготовка для новой инициативы."""
    out = {}
    for sec, _, ps in SECTIONS:
        out[sec] = {p.key: p.default for p in ps}
    out["channels"] = []
    return out


def _assert_consistent():
    """Реестр и SCHEMA движка обязаны описывать один и тот же набор ключей.

    Без этой проверки они разъедутся молча: параметр появится в движке, но не в форме,
    и человек не сможет его задать — либо наоборот, форма отправит ключ, который движок
    отвергнет строгой валидацией.
    """
    # Разделы со значением None в SCHEMA — вложенные: плоских диапазонов у них нет,
    # состав проверяется ниже против их собственных словарей в движке.
    nested_secs = {sec for sec, keys in calc.SCHEMA.items() if keys is None}
    if nested_secs != set(NESTED_SECTIONS):
        raise RuntimeError(
            "Вложенные разделы движка разошлись с NESTED_SECTIONS.\n"
            f"  только в движке:  {sorted(nested_secs - set(NESTED_SECTIONS))}\n"
            f"  только в реестре: {sorted(set(NESTED_SECTIONS) - nested_secs)}\n"
            "  Новый вложенный блок обязан быть описан в реестре, иначе о нём "
            "не узнают ни форма, ни книга, ни навык.")

    reg = {(p.section, p.key) for p in ALL}
    eng = {(sec, k) for sec, keys in calc.SCHEMA.items() if keys for k in keys
           if not (sec == "audience" and k == "segments")}
    unknown_nested = set(NESTED_KEYS) - eng
    if unknown_nested:
        raise RuntimeError(
            f"NESTED_KEYS ссылается на несуществующие ключи SCHEMA: {sorted(unknown_nested)}")
    eng -= set(NESTED_KEYS)
    if reg != eng:
        raise RuntimeError(
            "Реестр параметров разошёлся с calc.SCHEMA.\n"
            f"  только в реестре: {sorted(reg - eng)}\n"
            f"  только в движке:  {sorted(eng - reg)}\n"
            "  Скалярный параметр заводится в SECTIONS, вложенный — в NESTED_KEYS "
            "или NESTED_SECTIONS. Молча пропускать нельзя: параметр, которого нет "
            "в реестре, человек не сможет задать через форму.")
    ch_reg, ch_eng = set(CHANNEL_BY_KEY), set(calc.CHANNEL_KEYS)
    if ch_reg != ch_eng:
        raise RuntimeError(
            "Реестр каналов разошёлся с calc.CHANNEL_KEYS.\n"
            f"  только в реестре: {sorted(ch_reg - ch_eng)}\n"
            f"  только в движке:  {sorted(ch_eng - ch_reg)}")
    # id, reachable_share и existing_clients живут только внутри сегмента: в разделах
    # конфига их нет, поэтому в SECTIONS они не попадают, а в SEGMENT_KEYS есть
    scoped = {p.key for p in SEGMENT_SCOPED} | {"id", "reachable_share", "existing_clients"}
    if scoped != set(calc.SEGMENT_KEYS):
        raise RuntimeError(
            "Посегментные параметры разошлись с calc.SEGMENT_KEYS.\n"
            f"  только в реестре: {sorted(scoped - set(calc.SEGMENT_KEYS))}\n"
            f"  только в движке:  {sorted(set(calc.SEGMENT_KEYS) - scoped)}")


_assert_consistent()
