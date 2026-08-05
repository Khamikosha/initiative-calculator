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
    note: str = ""

    @property
    def rng(self) -> Optional[Tuple[float, float]]:
        # каналы — список, а не раздел конфига, поэтому их диапазоны лежат отдельно;
        # без этой ветки rng молча возвращал None для каждого параметра канала
        src = calc.CHANNEL_KEYS if self.section == "channels" else calc.SCHEMA.get(self.section, {})
        return src.get(self.key)

    def works_for(self, type_id) -> bool:
        """Влияет ли параметр на результат при этом типе инициативы."""
        return not self.types or type_id in self.types


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

# Продукт определяет возрастные правила банка — пороги живут в calc.PRODUCT_AGE_GATE
PRODUCTS = [
    ("account", "Счёт"), ("deposit", "Вклад"), ("piggy_bank", "Копилка"), ("card", "Карта"),
    ("savings", "Накопительный счёт"), ("broker", "Брокерский счёт"), ("prime", "Подписка"),
    ("loyalty", "Программа лояльности"), ("credit", "Кредит"), ("ecosystem", "Экосистема"),
]
PRODUCT_NAME = dict(PRODUCTS)

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
]

# ------------------------------------------------------------------ ВОРОНКА
FUNNEL = [
    P("approval_rate", "funnel", "Approval rate", unit="%", fmt="pct", default=0.60,
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля одобренных заявок. Карты 50–70%, кредиты 20–40%, вклады ~95%."),
    P("activation_rate", "funnel", "Activation rate", unit="%", fmt="pct", default=0.65,
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля одобренных, дошедших до целевого действия. Самая недооценённая дыра воронки."),
    P("cannibalization", "funnel", "Каннибализация", unit="%", fmt="pct", default=0.15,
      key_assumption=True, segment_scoped=True,
      note="Доля клиентов, переехавших со своих же продуктов. Ноль ставить только с обоснованием: "
           "в банке новый продукт почти всегда ест старый."),
    P("deadweight", "funnel", "Deadweight (взяли бы и так)", unit="%", fmt="pct", default=0.0,
      grade="D", segment_scoped=True, types=("pilot",), key_assumption=True,
      note="Доля тех, кто оформил бы и без промо. Для акций 30–60%, для обычных кампаний 0. "
           "«Скидка дала +40%» бессмысленно, если половина взяла бы и так."),
]

# ------------------------------------------------------------------ ЮНИТ-ЭКОНОМИКА
UNIT = [
    P("avg_balance", "unit_economics", "Средний баланс", unit="₽", fmt="money", default=45_000,
      grade="A", key_assumption=True, segment_scoped=True,
      note="Остаток на счёте. Источник процентной маржи."),
    P("nim_annual", "unit_economics", "NIM / FTP-спред (годовой)", unit="%", fmt="pct",
      default=0.07, grade="A", segment_scoped=True,
      note="Ставка, по которой банк зарабатывает на остатке."),
    P("monthly_turnover", "unit_economics", "Оборот по карте в месяц", unit="₽", fmt="money",
      default=28_000, grade="A", segment_scoped=True,
      note="База для интерчейнджа и кэшбэка."),
    P("interchange_rate", "unit_economics", "Ставка интерчейнджа", unit="%", fmt="pct2",
      default=0.011, grade="A", segment_scoped=True, note="Комиссия от оборота, обычно 0,8–1,4%."),
    P("fee_income_m", "unit_economics", "Прочий комиссионный доход", unit="₽/мес", fmt="money",
      default=0, segment_scoped=True, note="Подписка, СМС-информирование, страховки."),
    P("cashback_rate", "unit_economics", "Кэшбэк (доля оборота)", unit="%", fmt="pct2",
      default=0.005, grade="A", segment_scoped=True, note="Программа лояльности. Прямой вычет из маржи."),
    P("servicing_cost_m", "unit_economics", "Стоимость обслуживания", unit="₽/мес", fmt="money",
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
      grade="A", key_assumption=True, segment_scoped=True,
      note="Доля активных, отваливающихся за месяц. Определяет LTV. Ноль означает вечного клиента "
           "и завышает NPV."),
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

# ------------------------------------------------------------------ КАЧЕСТВО ДАННЫХ
GRADES = [
    P("A", "confidence", "Грейд A — свой факт из BI", unit="шт", fmt="int", default=0,
      note="Вес 1,00. Прошлые кампании этого же продукта, выгрузка из DWH."),
    P("B", "confidence", "Грейд B — свой аналог", unit="шт", fmt="int", default=0,
      note="Вес 0,75. Другой продукт, другая ЦА или период, но свои данные."),
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
      note="Показы = бюджет / CPM × 1000.", kinds=("perform", "brand")),
    P("ctr", "channels", "CTR", unit="%", fmt="pct2", default=0, note="Для perform-каналов.", kinds=("perform",)),
    P("response_rate", "channels", "Response rate", unit="%", fmt="pct2", default=0,
      note="Для internal-каналов. Для коммуникаций задавать как uplift над контрольной группой.", kinds=("internal",)),
    P("cr_to_app", "channels", "CR в заявку", unit="%", fmt="pct", default=0, kinds=("internal", "perform")),
    P("brand_uplift", "channels", "Brand uplift", unit="%", fmt="pct", default=0,
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
    ("funnel", "Воронка", FUNNEL),
    ("unit_economics", "Юнит-экономика клиента", UNIT),
    ("costs", "Затраты", COSTS),
    ("rice", "RICE — для сравнения инициатив между собой", RICE),
    ("confidence", "Качество данных — грейд источника каждого допущения", GRADES),
]

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
    reg = {(p.section, p.key) for p in ALL}
    eng = {(sec, k) for sec, keys in calc.SCHEMA.items() for k in keys
           if not (sec == "audience" and k == "segments")}
    if reg != eng:
        raise RuntimeError(
            "Реестр параметров разошёлся с calc.SCHEMA.\n"
            f"  только в реестре: {sorted(reg - eng)}\n"
            f"  только в движке:  {sorted(eng - reg)}")
    ch_reg, ch_eng = set(CHANNEL_BY_KEY), set(calc.CHANNEL_KEYS)
    if ch_reg != ch_eng:
        raise RuntimeError(
            "Реестр каналов разошёлся с calc.CHANNEL_KEYS.\n"
            f"  только в реестре: {sorted(ch_reg - ch_eng)}\n"
            f"  только в движке:  {sorted(ch_eng - ch_reg)}")
    scoped = {p.key for p in SEGMENT_SCOPED} | {"id", "reachable_share"}
    if scoped != set(calc.SEGMENT_KEYS):
        raise RuntimeError(
            "Посегментные параметры разошлись с calc.SEGMENT_KEYS.\n"
            f"  только в реестре: {sorted(scoped - set(calc.SEGMENT_KEYS))}\n"
            f"  только в движке:  {sorted(set(calc.SEGMENT_KEYS) - scoped)}")


_assert_consistent()
