#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движок расчёта эффекта инициатив (банк/финтех). Запуск: python3 calc.py config.json"""
import json, sys, math, copy, difflib

def g(d, k, default=0.0):
    v = d.get(k, default)
    return default if v is None else v


def nf(x, dec=0):
    """Число с пробелами в разрядах.

    Раньше разряды разделялись заменой запятых на пробелы во всей строке сообщения.
    Вместе с разделителями разрядов это съедало и обычные запятые текста:
    «1 220 000 клиентов  а не по медиаворонке». Форматировать надо ЧИСЛО,
    а не предложение вокруг него.
    """
    return format(x, f",.{dec}f").replace(",", " ")   # тонкая шпация как разделитель

# ------------------------------------------------------------------ ВОЗРАСТНЫЕ СЕГМЕНТЫ
SEGMENTS = {
    "kids":  {"name": "Дети (0–13)",       "age_min": 0,  "age_max": 13},
    "teens": {"name": "Подростки (14–17)", "age_min": 14, "age_max": 17},
    "youth": {"name": "Молодёжь (18–24)",  "age_min": 18, "age_max": 24},
}

# С какого возраста продукт доступен клиенту. Правила банка, а не допущения модели:
# до 14 лет счёт и вклад открывает третье лицо в офисе по свидетельству о рождении;
# с 6 лет — детская карта и копилки в детском приложении; с 14 — вход в банковское
# приложение, накопительные продукты, брокерский счёт, подписка и программа лояльности;
# с 18 — весь функционал банка и экосистемы.
# Формат: (с какого возраста доступен, по какой включительно). None сверху — границы нет.
#
# Верхняя граница нужна не для порядка: именной продукт живёт в своей возрастной вилке,
# а не «от порога и до бесконечности». «Детская карта» — это 6–13, в 14 клиент переходит
# на другой продукт. Без верхней границы модель молча включила бы подростков и молодёжь
# в детскую карту и посчитала бы их по чужому NPV продукта.
PRODUCT_AGE = {
    # категории без готового NPV продукта
    "account": (0, None), "deposit": (0, None), "piggy_bank": (6, None), "card": (6, None),
    "savings": (14, None), "broker": (14, None), "prime": (14, None), "loyalty": (14, None),
    "credit": (18, None), "ecosystem": (18, None),
    # Продукты линейки — возрастные рамки свои и у́же, чем у категории.
    # Детские ВКЛАДЫ доступны с рождения: до 14 лет их открывает родитель в офисе
    # по свидетельству о рождении. Детская КАРТА — только с 6 лет, ею пользуется
    # сам ребёнок. Отсюда разные нижние границы у карты и вкладов одного возраста.
    "kids_card": (6, 13),
    "kids_deposit": (0, 13),
    "kids_key_deposit": (0, 17),
    "youth_deposit": (14, None),
    # Кредитная карта — с 18: кредитные продукты несовершеннолетним недоступны.
    # Верхней границы не ставим: «молодёжная» это позиционирование, а не правило банка,
    # и выдумывать ограничение, которого нам не называли, нельзя — оно молча срезало бы
    # часть рынка.
    # Молодёжная карта — 14–24 по присланным данным. Верхнюю границу не ставим:
    # сегменты и так обрываются на 24, а жёсткая рамка сверху означала бы, что в 25
    # карту отбирают, — такого правила нам не называли.
    "youth_card": (14, None),
    "youth_credit_card": (18, None),
}
PRODUCT_AGE_GATE = {k: lo for k, (lo, _) in PRODUCT_AGE.items()}   # только нижний порог

# Продукты, где банк несёт кредитный риск и Cost of Risk обязателен. Для всех прочих
# CoR = 0 — законное значение, а не упущение. Список расширяется вместе с каталогом.
CREDIT_PRODUCTS = {"credit", "youth_credit_card"}
# Продукты, которые для детей оформляются только в офисе и только третьим лицом
BRANCH_ONLY_UNDER_14 = {"account", "deposit", "kids_deposit", "kids_key_deposit"}
APP_AGE = 14                       # доступ в банковское приложение


def segment_availability(seg_id, product):
    """Доля возрастов сегмента, которым продукт доступен, и минимальный доступный возраст."""
    s = SEGMENTS[seg_id]
    gate, top = PRODUCT_AGE.get(product, (0, None))
    lo = max(s["age_min"], gate)
    hi = s["age_max"] if top is None else min(s["age_max"], top)
    span = s["age_max"] - s["age_min"] + 1
    return max(0, hi - lo + 1) / span, lo


def _wavg(pairs):
    """Взвешенное среднее. pairs — список (вес, значение)."""
    w = sum(p[0] for p in pairs)
    return sum(p[0] * p[1] for p in pairs) / w if w else 0.0


def resolve_segments(cfg):
    """Сводит выбранные возрастные сегменты в ОДИН взвешенный набор допущений.

    Веса подобраны так, чтобы доходные компоненты маржи считались точно, а не приблизительно:
    ставки (NIM, интерчейндж, кэшбэк, CoR) взвешиваются по базе, к которой применяются
    (баланс или оборот), поэтому баланс_средн × NIM_средн = среднему на клиента по сегментам.
    Приблизительными остаются отток и ramp-up — у них нет такой мультипликативной базы.
    """
    raw = cfg.get("audience", {}).get("segments")
    if not raw:
        return cfg
    cfg = copy.deepcopy(cfg)
    aud, fun, ue, kt = cfg["audience"], cfg["funnel"], cfg["unit_economics"], cfg["costs"]
    product = cfg.get("meta", {}).get("product", "")
    notes, rows = [], []

    for item in raw:
        s = {"id": item} if isinstance(item, str) else dict(item)
        sid = s.get("id")
        if sid not in SEGMENTS:
            notes.append(f"ОШИБКА ВВОДА: неизвестный сегмент «{sid}». Допустимые: {', '.join(SEGMENTS)}.")
            continue
        meta = SEGMENTS[sid]
        avail, lo = segment_availability(sid, product)
        size = g(s, "target_size", aud.get("target_size", 0) if len(raw) == 1 else 0)
        # доля сегмента, до которой инициатива физически дотягивается; по умолчанию —
        # доля подходящих возрастов при равномерном распределении по годам
        share = s["reachable_share"] if s.get("reachable_share") is not None else avail
        eff = size * share
        # доли и эффективная ЦА НЕ округляются: на них же строится лист «Сегменты» в Excel,
        # а округление до 4 знаков разводит движок и книгу на сотни рублей NPV
        row = {"id": sid, "name": meta["name"], "target_size": size,
               "available_share": avail, "min_age": lo,
               "reachable_share": share, "effective_audience": eff,
               "organic_apps_m": g(s, "baseline_organic_apps_m",
                                   aud.get("baseline_organic_apps_m", 0) if len(raw) == 1 else 0),
               "approval_rate": g(s, "approval_rate", g(fun, "approval_rate", 1.0)),
               "activation_rate": g(s, "activation_rate", g(fun, "activation_rate", 1.0)),
               "cannibalization": g(s, "cannibalization", g(fun, "cannibalization")),
               "deadweight": g(s, "deadweight", g(fun, "deadweight")),
               "acq_cost_per_client": g(s, "acq_cost_per_client", g(kt, "acq_cost_per_client")),
               "existing_clients": g(s, "existing_clients")}
        for k in ("avg_balance", "nim_annual", "monthly_turnover", "interchange_rate", "fee_income_m",
                  "cashback_rate", "servicing_cost_m", "cor_annual", "ramp_months", "monthly_churn",
                  "value_per_client", "operating_income_annual"):
            row[k] = g(s, k, g(ue, k, 1.0 if k == "ramp_months" else 0.0))
        if avail <= 0:
            notes.append(f"СЕГМЕНТ ИСКЛЮЧЁН: «{meta['name']}» — продукт «{product}» доступен "
                         f"только с {PRODUCT_AGE_GATE.get(product, 0)} лет, весь сегмент младше. "
                         "Его аудитория, заявки и маржа в расчёт не попадают.")
        elif size <= 0:
            notes.append(f"ОШИБКА ВВОДА: у сегмента «{meta['name']}» размер ЦА = 0 — "
                         "он не влияет на результат. Задайте target_size.")
        elif s.get("reachable_share") is None and avail < 1:
            notes.append(f"ДОПУЩЕНИЕ: в сегменте «{meta['name']}» продукт доступен с {lo} лет, "
                         f"поэтому взято {avail:.0%} сегмента. Это равномерное распределение по годам — "
                         "замените reachable_share своей демографией.")
        if sid == "kids" and product in BRANCH_ONLY_UNDER_14:
            notes.append(f"КАНАЛ: «{meta['name']}» — счёт и вклад до 14 лет открывает третье лицо "
                         "в офисе по свидетельству о рождении. Заявитель — родитель, а не ребёнок: "
                         "медийная ЦА и конверсия в заявку должны быть родительскими.")
        rows.append(row)

    live = [r for r in rows if r["effective_audience"] > 0 and r["available_share"] > 0]
    if not live:
        notes.append("ОШИБКА ВВОДА: ни один выбранный сегмент не даёт аудитории — считать нечего.")
        cfg["_segments"] = {"rows": rows, "notes": notes}
        return cfg
    if any(r["id"] == "kids" for r in live) and any(
            c.get("kind") == "internal" for c in cfg.get("channels", [])):
        notes.append(f"КАНАЛ: дети до {APP_AGE} лет не имеют доступа в банковское приложение — "
                     "push и in-app до них не доходят. Внутренние каналы бьют в родителя: "
                     "проверьте, что reach_abs — это родительская база, а не число детей.")

    # веса: аудитория → одобренные → клиенты; ставки — по базе, к которой применяются
    for r in live:
        r["_appr"] = r["effective_audience"] * r["approval_rate"]
        r["_clients"] = r["_appr"] * r["activation_rate"]
    aud_w = [(r["effective_audience"], r) for r in live]
    appr_w = [(r["_appr"], r) for r in live]
    cl_w = [(r["_clients"], r) for r in live]
    bal_w = [(r["_clients"] * r["avg_balance"], r) for r in live]
    turn_w = [(r["_clients"] * r["monthly_turnover"], r) for r in live]

    aud["target_size"] = sum(r["effective_audience"] for r in live)
    aud["baseline_organic_apps_m"] = sum(r["organic_apps_m"] for r in live)
    fun["approval_rate"] = _wavg([(w, r["approval_rate"]) for w, r in aud_w])
    fun["activation_rate"] = _wavg([(w, r["activation_rate"]) for w, r in appr_w])
    for k in ("cannibalization", "deadweight"):
        fun[k] = _wavg([(w, r[k]) for w, r in cl_w])
    # NPV продукта взвешивается по числу клиентов: суммарный NPV = Σ клиенты_i × NPV_i,
    # поэтому среднее по клиентам даёт ту же сумму точно, а не приблизительно
    for k in ("avg_balance", "monthly_turnover", "fee_income_m", "servicing_cost_m",
              "ramp_months", "monthly_churn", "value_per_client",
              "operating_income_annual"):
        ue[k] = _wavg([(w, r[k]) for w, r in cl_w])
    for k, base in (("nim_annual", bal_w), ("cor_annual", bal_w),
                    ("interchange_rate", turn_w), ("cashback_rate", turn_w)):
        ue[k] = _wavg([(w, r[k]) for w, r in base])
    kt["acq_cost_per_client"] = _wavg([(w, r["acq_cost_per_client"]) for w, r in cl_w])

    # Проникновение: сколько рынка сегмента уже клиенты банка. При высоком проникновении
    # «привлечение» на деле оказывается кросс-продажей: новых людей взять неоткуда.
    for r in rows:
        r["penetration"] = (r["existing_clients"] / r["target_size"]
                            if r["existing_clients"] and r["target_size"] else None)
    for r in live:
        if r["penetration"] and r["penetration"] >= 0.5:
            free = r["target_size"] - r["existing_clients"]
            notes.append(
                f"ПРОНИКНОВЕНИЕ: в сегменте «{r['name']}» уже {r['penetration']:.0%} рынка — "
                f"клиенты банка. Незанятого рынка остаётся {nf(free)} чел из "
                f"{nf(r['target_size'])}. Модель считает охват против ВСЕЙ ЦА, поэтому если "
                "инициатива про привлечение НОВЫХ клиентов, инкремент завышен: возьмите "
                "target_size равным незанятому рынку. Если инициатива про кросс-продажу "
                "своей базе — всё верно, но растёт ОД, а не АКБ."
                )

    total = sum(r["_clients"] for r in live)
    for r in rows:
        r["client_weight"] = round(r.get("_clients", 0) / total, 4) if total else 0
        r.pop("_appr", None); r.pop("_clients", None)
    cfg["_segments"] = {"rows": rows, "notes": notes,
                        "effective_audience_total": aud["target_size"]}
    return cfg


# ------------------------------------------------------------------ СХЕМА КОНФИГА
# `g()` возвращает дефолт для любого отсутствующего ключа. Пока конфиг пишет один человек,
# это удобно; когда его собирает веб-форма и сто человек — это главный класс ошибок:
# «cannibalisation» вместо «cannibalization» молча превращается в каннибализацию 0%
# и завышает результат, не сказав ни слова. Модель считает без ошибок и считает не то.
#
# Диапазоны — границы физического смысла, а не «нормы». Их задача — ловить проценты,
# введённые как 15 вместо 0,15. None означает свободное значение (текст или список).
_RATE = (0.0, 1.0)
_MONEY = (0.0, 1e12)

UE_KEYS = {"avg_balance": (0.0, 1e9), "nim_annual": (-1.0, 1.0), "monthly_turnover": (0.0, 1e9),
           "interchange_rate": _RATE, "fee_income_m": (0.0, 1e7), "cashback_rate": _RATE,
           "servicing_cost_m": (0.0, 1e6), "cor_annual": _RATE,
           "ramp_months": (0.0, 120.0), "monthly_churn": _RATE,
           "value_per_client": (0.0, 1e7), "operating_income_annual": (0.0, 1e7)}
FUNNEL_KEYS = {"approval_rate": _RATE, "activation_rate": _RATE,
               "cannibalization": _RATE, "deadweight": _RATE}

SCHEMA = {
    "meta": {"name": None, "type": None, "product": None,
             "currency": None, "fx_to_rub": (0.0, 1e6),
             "horizon_months": (1, 600), "discount_rate_annual": (0.0, 2.0)},
    "audience": {"target_size": (0.0, 2e9), "baseline_organic_apps_m": (0.0, 1e9),
                 "halo_months": (0.0, 60.0), "halo_decay": _RATE, "segments": None,
                 "reachable_market": (0.0, 2e9), "market_total": (0.0, 2e9)},
    "funnel": dict(FUNNEL_KEYS),
    "unit_economics": {**UE_KEYS, "mode": None, "value_horizon_months": (1, 600),
                       "turnover_split": None, "balance_to_turnover": (0.0, 10.0)},
    "acquisition": {"year1_target": (0.0, 1e9), "yoy_growth": (0.0, 5.0), "seasonality": None},
    # Третья сущность эффекта: ТОТ ЖЕ человек, ТОТ ЖЕ продукт, другое поведение.
    # Все поля скалярные, поэтому это обычный раздел — и веб-форма получает его даром.
    "natural_effect": None,     # вложенные объекты: parts и adjustments
    "base_activation": {
        "name": None, "eligible_clients": (0.0, 2e9), "reach_share": _RATE,
        "technically_reachable": _RATE, "parts": None,
        "workout_months": (0.0, 600.0), "monthly_inflow": (0.0, 1e9),
        "uplift_rate": _RATE, "measured_against_control": None,
        "turnover_before": (0.0, 1e9), "turnover_after": (0.0, 1e9),
        "balance_before": (0.0, 1e9), "balance_after": (0.0, 1e9),
        "decay_months": (0.0, 600.0), "attribution_share": _RATE,
    },
    "brief": None,              # проверяется отдельно: есть вложенные объекты
    "pilot": None,
    "linked_client": None,
    "companion": None,
    "partner": None,
    "costs": {"production": _MONEY, "it_dev": _MONEY, "one_off_other": _MONEY,
              "opex_monthly": _MONEY, "campaign_months": (1, 600),
              "acq_cost_per_client": (0.0, 1e7)},
    "rice": {"reach": (0.0, 1e9), "impact": (0.0, 10.0), "confidence": _RATE,
             "effort": (0.0, 1e4), "hurdle_ltv_cac": (0.5, 20.0)},
    "confidence": {k: (0, 1000) for k in ("A", "B", "B_minus", "C", "D")},
}
CHANNEL_KEYS = {"name": None, "kind": None, "frk_tier": (1, 4), "budget": _MONEY,
                "cpm": (0.0, 1e6), "ctr": _RATE, "cr_to_app": _RATE, "response_rate": _RATE,
                "brand_uplift": (0.0, 5.0), "reach_abs": (0.0, 2e9), "impressions": (0.0, 1e13),
                "effective_freq_target": (0.1, 50.0)}
SEGMENT_KEYS = {"id": None, "target_size": (0.0, 2e9), "reachable_share": _RATE,
                "existing_clients": (0.0, 2e9),
                "baseline_organic_apps_m": (0.0, 1e9), "acq_cost_per_client": (0.0, 1e7),
                **FUNNEL_KEYS, **UE_KEYS}
CHANNEL_KINDS = ("internal", "perform", "brand")

# --- связанный клиент ---------------------------------------------------------
# Инициатива приносит деньги не только на носителе продукта. Детская карта живёт
# на родителе: он не уходит из банка, возвращается, если ушёл, и покупает больше,
# потому что банк теперь знает, что у него есть ребёнок. Три РАЗНЫЕ механики
# с разной проверяемостью — усреднять их в один «uplift» нельзя.
LINKED_KEYS = {"name": None, "clients_per_linked": (0.01, 100.0),
               "income_annual": (0.0, 1e10), "margin_rate": _RATE,
               "attribution_share": _RATE, "sources": None,
               "churn_annual": _RATE, "churn_reduction": _RATE,
               "selection_haircut": _RATE, "cross_sell_uplift": (0.0, 2.0)}
# Откуда берётся связанный клиент. Доли ОДНОГО плана, в сумме 1.0 — а не три
# независимых плана: независимые не сходятся и дают отрицательных «новых».
LINKED_SOURCE_KEYS = {"own_base": _RATE, "reactivated": _RATE, "new": _RATE}

# --- натуральный эффект -------------------------------------------------------
# Что меряем и почём это в деньгах банка. Разделение обязательно: снижение просрочки,
# сокращение обращений и сэкономленные часы — не рубли P&L, и подстановка их в итог
# завышает эффект в разы.
NATURAL_KEYS = {"name": None, "unit": None, "baseline": (0.0, 1e15),
                "monthly_inflow": (0.0, 1e15), "workout_months": (0.0, 600.0),
                "uplift_rate": _RATE, "measured_against_control": None,
                "monetization": (0.0, 10.0), "relapse_share": _RATE,
                "parts": None, "adjustments": None}
# Часть неоднородной базы: своя доля, свой потолок эффекта и своя ставка перевода
# в деньги. 90+ дней просрочки диалогом не лечится ни при каком качестве агента.
NATURAL_PART_KEYS = {"name": None, "share": _RATE, "uplift_cap": _RATE,
                     "monetization": (0.0, 10.0)}
# Именованный вычет: инструмент помощи почти всегда чего-то стоит. Ставка — доля
# от натурального эффекта, обычно отрицательная.
NATURAL_ADJ_KEYS = {"name": None, "rate": (-10.0, 10.0)}

# --- продукт-спутник ----------------------------------------------------------
COMPANION_KEYS = {"name": None, "attach_rate": _RATE, "funded_rate": _RATE,
                  "avg_balance": (0.0, 1e10), "nim_annual": (-1.0, 1.0),
                  "balance_growth_m": (0.0, 1.0), "spend_share": _RATE,
                  "spend_returns_to_turnover": None}

# --- партнёр ------------------------------------------------------------------
PARTNER_KEYS = {"name": None, "turnover_split": None, "fee_per_card": (0.0, 1e7),
                "cost_per_card": (0.0, 1e7)}

# Разложение оборота по типам операций: у POS, e-POS и снятия разные ставки,
# и один усреднённый interchange_rate прячет это различие. Доли — в сумме 1.0.
TXN_KEYS = {"share": _RATE, "fee": _RATE}

# --- разбор брифа -------------------------------------------------------------
# Человек описывает инициативу словами: «спецдизайн, партнёры со скидками, был пилот
# в Москве, выдаём по всей стране». Раньше из этого в модель попадало ровно одно —
# название. Остальное испарялось молча, а человек оставался уверен, что его пилот
# и его партнёров посчитали. Тихая потеря данных дороже неверной цифры: неверную
# видно, а эту нет.
#
# Поэтому каждое утверждение из описания обязано получить статус:
#   applied  — стало параметром (обязателен maps_to)
#   question — нужен ответ человека, без него посчитать нельзя
#   ignored  — сознательно не учитываем (обязателен reason)
# Всё, что не applied, печатается в отчёте отдельным списком.
BRIEF_KEYS = {"description": None, "claims": None}
CLAIM_KEYS = {"claim": None, "implication": None, "maps_to": None,
              "grade": None, "status": None, "reason": None}
CLAIM_STATUS = ("applied", "question", "ignored")

# --- пилот как ИСТОЧНИК ДАННЫХ ------------------------------------------------
# «Пилот» в meta.type означает «оцениваем, стоит ли делать пилот». «У нас уже был
# пилот» — совсем другое: это факт, которым калибруется модель. Две разные сущности
# лежали в одном поле. Пилот, который провели и не использовали в расчёте, —
# это выброшенные деньги.
PILOT_KEYS = {"name": None, "geography": None, "rollout_geography": None,
              "audience": None, "control_group": None,
              "sample_size": (0.0, 1e9), "control_size": (0.0, 1e9),
              "duration_months": (0.0, 600.0),
              "baseline_rate": _RATE, "observed_rate": _RATE,
              "scale_haircut": (0.0, 2.0), "metrics": None, "metrics_are": None,
              "randomized": None, "matched_at_baseline": None,
              "metric_cv": (0.0, 20.0),
              "tail_months": (0.0, 600.0), "metrics_tail": None, "groups": None,
              "kind": None}

# Что пилот мерил. Вопрос задаётся ПЕРВЫМ и решает, какие поля вообще показывать:
# у конверсионного пилота нет групповых замеров, у поведенческого нет конверсии.
# Без этого разделения шаг просит шестнадцать полей у человека, которому нужны шесть,
# и он не знает, какие именно.
PILOT_KINDS = ("conversion", "behavioral")

# Замер по группе: абсолютные уровни на начало и конец пилота. Дельту движок считает
# сам, и не одну, а две — и в этом весь смысл.
#
# Разница «до и после» ВНУТРИ группы эффектом не является: за восемь месяцев клиенты
# стали старше, прошёл сезон, изменился рынок. Всё это попадёт в «рост», если сравнивать
# группу с самой собой. Контрольная группа прожила ровно те же восемь месяцев, поэтому
# эффект — это разница разниц: (конец−начало) теста минус (конец−начало) контроля.
#
# Побочная выгода той же арифметики: разница между группами НА СТАРТЕ должна быть около
# нуля, если группы выровняли. Это прямая проверка того, что выравнивание сработало,
# и она бесплатна.
PILOT_GROUP_KEYS = {"name": None, "role": None, "metric": None,
                    "value_start": (-1e12, 1e12), "value_end": (-1e12, 1e12)}
PILOT_ROLES = ("test", "control")

# Метрики-УРОВНИ: абсолютные величины, а не доли. С ними у пилота с контрольной
# группой есть развилка, которой нет у долей, и движок не имеет права проходить
# её молча.
#
# «Средний оборот в пилоте 34 000» — это уровень: столько тратит клиент продукта,
# и подставить это в модель правильно. «Оборот вырос на 6 000 против клиентов
# с обычной картой» — это ПРИРОСТ ПРОТИВ КОНТРОЛЯ, и подставлять его как уровень
# нельзя ни при каких условиях: получится клиент, который тратит 6 000 в месяц.
#
# Числа выглядят одинаково, обе фразы звучат как «пилот показал», и разницу
# знает только тот, кто пилот проводил. Поэтому спрашиваем прямо.
PILOT_LEVEL_METRICS = {"avg_balance", "monthly_turnover", "fee_income_m",
                       "servicing_cost_m", "operating_income_annual",
                       "value_per_client"}
PILOT_METRICS_ARE = ("level", "uplift")
# Какие параметры пилот имеет право переопределить и куда они ложатся.
PILOT_TARGET = {**{k: "funnel" for k in FUNNEL_KEYS},
                **{k: "unit_economics" for k in UE_KEYS}}

# Два способа задать экономику клиента.
#   build_up        — собрать снизу вверх: баланс, оборот, ставки, отток, ramp. Даёт помесячную
#                     кривую, срок окупаемости и богатый торнадо, но требует десяти допущений.
#   value_per_client — взять готовую NPV продукта на клиента одним числом из
#                     валидированной модели продукта. Меньше допущений и выше грейд источника,
#                     но помесячной кривой нет, а значит нет и срока окупаемости.
MODE_BUILD_UP, MODE_VALUE = "build_up", "value_per_client"
MODES = (MODE_BUILD_UP, MODE_VALUE)


def is_value_mode(cfg):
    return g(cfg.get("unit_economics", {}), "mode", MODE_BUILD_UP) == MODE_VALUE


class ConfigError(ValueError):
    """Конфиг не прошёл проверку. Сообщение содержит ВСЕ найденные проблемы разом —
    чинить по одной за прогон слишком долго, когда конфиг приходит из формы."""


def _hint(key, allowed):
    near = difflib.get_close_matches(key, list(allowed), n=1, cutoff=0.6)
    if near:
        return f" Возможно, имелось в виду «{near[0]}»."
    return f" Допустимые: {', '.join(sorted(allowed))}."


def _check_keys(d, allowed, where, errs):
    for k, v in d.items():
        if k not in allowed:
            errs.append(f"{where}: неизвестный ключ «{k}».{_hint(k, allowed)}")
            continue
        rng = allowed[k]
        if rng is None or v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errs.append(f"{where}.{k}: ожидалось число, получено {type(v).__name__} ({v!r}).")
        elif not rng[0] <= v <= rng[1]:
            tail = " Проценты задаются долей: 15% это 0.15." if rng is _RATE and v > 1 else ""
            errs.append(f"{where}.{k} = {v} вне диапазона {rng[0]}…{rng[1]}.{tail}")


def _check_split(split, where, errs):
    """Разложение оборота по типам операций: словарь {имя: {share, fee}}, доли в сумме 1.0."""
    if split is None:
        return
    if not isinstance(split, dict):
        errs.append(f"{where}: должен быть объектом {{тип: {{share, fee}}}}, "
                    f"получен {type(split).__name__}.")
        return
    total = 0.0
    for name, part in split.items():
        if not isinstance(part, dict):
            errs.append(f"{where}.{name}: должен быть объектом с ключами share и fee.")
            continue
        _check_keys(part, TXN_KEYS, f"{where}.{name}", errs)
        v = part.get("share")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += v
    if split and abs(total - 1.0) > 0.005:
        errs.append(f"{where}: доли операций в сумме дают {total:.3f}, а должны 1.0. "
                    "Недостающая доля оборота молча не приносит комиссии, "
                    "а лишняя приносит её дважды.")


def validate_config(cfg):
    """Строгая проверка: неизвестный ключ или значение вне диапазона — ошибка, а не дефолт."""
    if not isinstance(cfg, dict):
        raise ConfigError("Конфиг должен быть JSON-объектом.")
    mode = (cfg.get("unit_economics") or {}).get("mode")
    errs, known = [], set(SCHEMA) | {"channels"}
    if mode is not None and mode not in MODES:
        errs.append(f"unit_economics.mode = «{mode}» — допустимы только {', '.join(MODES)}.")
    for sec in sorted(set(cfg) - known):
        errs.append(f"Неизвестный раздел «{sec}».{_hint(sec, known)}")
    for sec, allowed in SCHEMA.items():
        val = cfg.get(sec)
        if val is None or allowed is None:
            continue
        if not isinstance(val, dict):
            errs.append(f"Раздел «{sec}» должен быть объектом, получен {type(val).__name__}.")
        else:
            _check_keys(val, allowed, sec, errs)

    # разделы с вложенными объектами — своя проверка
    for sec, allowed in (("linked_client", LINKED_KEYS), ("companion", COMPANION_KEYS),
                         ("partner", PARTNER_KEYS), ("brief", BRIEF_KEYS),
                         ("pilot", PILOT_KEYS), ("natural_effect", NATURAL_KEYS)):
        val = cfg.get(sec)
        if val is None:
            continue
        if not isinstance(val, dict):
            errs.append(f"Раздел «{sec}» должен быть объектом, получен {type(val).__name__}.")
            continue
        _check_keys(val, allowed, sec, errs)
    lc = cfg.get("linked_client") or {}
    if isinstance(lc, dict) and lc.get("sources") is not None:
        src = lc["sources"]
        if not isinstance(src, dict):
            errs.append("linked_client.sources: должен быть объектом "
                        "{own_base, reactivated, new}.")
        else:
            _check_keys(src, LINKED_SOURCE_KEYS, "linked_client.sources", errs)
            tot = sum(v for v in src.values()
                      if isinstance(v, (int, float)) and not isinstance(v, bool))
            if abs(tot - 1.0) > 0.005:
                errs.append(
                    f"linked_client.sources: доли источников в сумме дают {tot:.3f}, "
                    "а должны 1.0. Это доли ОДНОГО плана. Если задавать источники "
                    "независимыми планами, они не сойдутся: на живой модели «новые» "
                    "получались вычитанием и уходили в минус на 32 месяца из 36.")
    # Каждое утверждение брифа обязано иметь судьбу. Незаполненный статус — не мелочь
    # оформления: именно так утверждение и теряется, оставаясь «вроде бы учтённым».
    claims = (cfg.get("brief") or {}).get("claims")
    if claims is not None:
        if not isinstance(claims, list):
            errs.append("brief.claims: должен быть списком утверждений из описания.")
        else:
            for i, cl in enumerate(claims):
                where = f"brief.claims[{i}]"
                if not isinstance(cl, dict):
                    errs.append(f"{where}: должен быть объектом.")
                    continue
                _check_keys(cl, CLAIM_KEYS, where, errs)
                if not cl.get("claim"):
                    errs.append(f"{where}.claim: пустая цитата из описания.")
                st = cl.get("status")
                if st not in CLAIM_STATUS:
                    errs.append(f"{where}.status = «{st}» — допустимы только "
                                f"{', '.join(CLAIM_STATUS)}. Утверждение без статуса — "
                                "это и есть потерянное утверждение.")
                elif st == "applied" and not cl.get("maps_to"):
                    errs.append(f"{where}: статус applied, но не указан maps_to. "
                                "«Учтено» без указания параметра проверить нельзя.")
                elif st == "ignored" and not cl.get("reason"):
                    errs.append(f"{where}: статус ignored, но не указана reason. "
                                "Сознательно выброшенное утверждение требует причины — "
                                "иначе это не решение, а недосмотр.")
                gr = cl.get("grade")
                if gr is not None and gr not in GRADE_W:
                    errs.append(f"{where}.grade = «{gr}» — допустимы {', '.join(GRADE_W)}.")

    pil = cfg.get("pilot")
    if isinstance(pil, dict):
        mt = pil.get("metrics")
        if mt is not None:
            if not isinstance(mt, dict):
                errs.append("pilot.metrics: должен быть объектом {параметр: значение}.")
            else:
                _check_keys(mt, {**FUNNEL_KEYS, **UE_KEYS}, "pilot.metrics", errs)
        _kind = pil.get("kind")
        if _kind is not None and _kind not in PILOT_KINDS:
            errs.append(f"pilot.kind: «conversion» (мерили конверсию) или «behavioral» "
                        f"(мерили поведение клиентов), получено «{_kind}».")
        _has_groups = bool(pil.get("groups"))
        _has_rates = bool(g(pil, "observed_rate") or g(pil, "baseline_rate"))
        if _kind == "conversion" and _has_groups:
            errs.append("pilot.groups: пилот объявлен конверсионным, но заполнены "
                        "групповые замеры. Одно из двух лишнее — выберите, что мерили.")
        if _kind == "behavioral" and _has_rates:
            errs.append("pilot.baseline_rate / observed_rate: пилот объявлен "
                        "поведенческим, но заполнена конверсия. Одно из двух лишнее — "
                        "выберите, что мерили.")
        _ma = pil.get("metrics_are")
        if _ma is not None and _ma not in PILOT_METRICS_ARE:
            errs.append(f"pilot.metrics_are: допустимо «level» (уровень в тестовой группе) "
                        f"или «uplift» (прирост против контроля), получено «{_ma}».")
        _grp = pil.get("groups")
        if _grp is not None:
            if not isinstance(_grp, list):
                errs.append("pilot.groups: должен быть списком замеров по группам.")
            else:
                for i, gr in enumerate(_grp):
                    if not isinstance(gr, dict):
                        errs.append(f"pilot.groups[{i}]: должен быть объектом.")
                        continue
                    _check_keys(gr, PILOT_GROUP_KEYS, f"pilot.groups[{i}]", errs)
                    if gr.get("role") not in PILOT_ROLES:
                        errs.append(f"pilot.groups[{i}].role: «test» или «control», "
                                    f"получено «{gr.get('role')}».")
                    if gr.get("metric") not in PILOT_TARGET:
                        errs.append(f"pilot.groups[{i}].metric: «{gr.get('metric')}» — "
                                    "движок такого параметра не знает.")
        _tail = pil.get("metrics_tail")
        if _tail is not None:
            if not isinstance(_tail, dict):
                errs.append("pilot.metrics_tail: должен быть объектом {параметр: значение "
                            "за последние месяцы окна}.")
            else:
                _unknown = sorted(set(_tail) - set(pil.get("metrics") or {}))
                if _unknown:
                    errs.append(f"pilot.metrics_tail: {', '.join(_unknown)} — нет среди "
                                "измеренных метрик. Хвост сравнивается со средней "
                                "за окно, поэтому у него должна быть пара.")
                _tm, _dm = g(pil, "tail_months"), g(pil, "duration_months")
                if _tail and not (0 < _tm < _dm):
                    errs.append(
                        f"pilot.tail_months: {_tm:g} при длительности замера {_dm:g} — "
                        "хвост это последние НЕСКОЛЬКО месяцев окна, он должен быть "
                        "больше нуля и меньше самого окна.")
        _ctrl_row = any(isinstance(gr, dict) and gr.get("role") == "control"
                        for gr in (pil.get("groups") or []))
        _has_ctrl = bool(pil.get("control_group")) or _ctrl_row
        _lvl = sorted(set(pil.get("metrics") or {}) & PILOT_LEVEL_METRICS)
        if _lvl and _has_ctrl and _ma is None and not _has_groups:
            errs.append(
                "pilot.metrics_are: у пилота есть контрольная группа и метрики-уровни "
                f"({', '.join(_lvl)}) — непонятно, что в них записано. «Средний оборот "
                "в пилоте 34 000» и «оборот вырос на 6 000 против контроля» выглядят "
                "одинаково, а означают разное: первое подставляется в модель как есть, "
                "второе как уровень даст клиента, который тратит 6 000 в месяц. "
                "Укажите явно: metrics_are = «level» или «uplift».")
        for a, b in (("sample_size", "control_size"),):
            if pil.get("control_group") and not pil.get(b) and pil.get(a):
                errs.append(f"pilot.{b}: заявлена контрольная группа, но её размер не задан — "
                            "без него нельзя проверить, мог ли пилот вообще поймать эффект.")

    for _sec in ("natural_effect", "base_activation"):
        _blk = cfg.get(_sec)
        if not isinstance(_blk, dict):
            continue
        _prts = _blk.get("parts")
        if _prts is None:
            continue
        if not isinstance(_prts, list):
            errs.append(f"{_sec}.parts: должен быть списком частей базы.")
            continue
        _tot = 0.0
        for i, pt in enumerate(_prts):
            if not isinstance(pt, dict):
                errs.append(f"{_sec}.parts[{i}]: должен быть объектом.")
                continue
            _check_keys(pt, NATURAL_PART_KEYS, f"{_sec}.parts[{i}]", errs)
            v = pt.get("share")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                _tot += v
        if _prts and abs(_tot - 1.0) > 0.005:
            errs.append(
                f"{_sec}.parts: доли частей базы в сумме дают {_tot:.3f}, а должны 1.0. "
                "Недостающая часть базы молча не даёт эффекта, а лишняя даёт его дважды.")

    ne = cfg.get("natural_effect")
    if isinstance(ne, dict):
        # Проверка `parts` живёт в общем цикле выше — он покрывает и natural_effect,
        # и base_activation одним правилом. Здесь оставался её огрызок от рефакторинга:
        # `prts = None` и условие, которое никогда не истинно. Двадцать строк мёртвой
        # проверки читались как работающая защита.
        adjs = ne.get("adjustments")
        if adjs is not None:
            if not isinstance(adjs, list):
                errs.append("natural_effect.adjustments: должен быть списком вычетов.")
            else:
                for i, a in enumerate(adjs):
                    if not isinstance(a, dict):
                        errs.append(f"natural_effect.adjustments[{i}]: должен быть объектом.")
                        continue
                    _check_keys(a, NATURAL_ADJ_KEYS, f"natural_effect.adjustments[{i}]", errs)
                    if not a.get("name"):
                        errs.append(f"natural_effect.adjustments[{i}]: вычет без названия. "
                                    "Безымянная поправка через месяц необъяснима.")

    _check_split((cfg.get("unit_economics") or {}).get("turnover_split"),
                 "unit_economics.turnover_split", errs)
    _check_split((cfg.get("partner") or {}).get("turnover_split"),
                 "partner.turnover_split", errs)

    seas = (cfg.get("acquisition") or {}).get("seasonality")
    if seas is not None:
        if not isinstance(seas, list) or not all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in seas):
            errs.append("acquisition.seasonality: должен быть списком из 12 чисел "
                        "(множители по месяцам, среднее ≈ 1).")
        elif len(seas) != 12:
            errs.append(f"acquisition.seasonality: получено {len(seas)} значений, нужно 12 — "
                        "профиль задаётся на 12 месяцев ОТ СТАРТА кампании (не от января) "
                        "и повторяется дальше.")
        elif sum(seas) <= 0:
            errs.append("acquisition.seasonality: сумма множителей = 0, притока не будет.")

    chans = cfg.get("channels") or []
    if not isinstance(chans, list):
        errs.append("«channels» должен быть списком.")
    else:
        for i, ch in enumerate(chans):
            where = f"channels[{i}]"
            if not isinstance(ch, dict):
                errs.append(f"{where}: должен быть объектом.")
                continue
            _check_keys(ch, CHANNEL_KEYS, where, errs)
            kind = ch.get("kind", "perform")
            if kind not in CHANNEL_KINDS:
                errs.append(f"{where}.kind = «{kind}» — допустимы только "
                            f"{', '.join(CHANNEL_KINDS)}.")

    segs = (cfg.get("audience") or {}).get("segments") or []
    if segs and not isinstance(segs, list):
        errs.append("«audience.segments» должен быть списком.")
    elif isinstance(segs, list):
        for i, s in enumerate(segs):
            where = f"audience.segments[{i}]"
            sid = s if isinstance(s, str) else (s.get("id") if isinstance(s, dict) else None)
            if isinstance(s, dict):
                _check_keys(s, SEGMENT_KEYS, where, errs)
            elif not isinstance(s, str):
                errs.append(f"{where}: должен быть объектом или строкой-идентификатором.")
            if sid is not None and sid not in SEGMENTS:
                errs.append(f"{where}.id = «{sid}» — допустимы только {', '.join(SEGMENTS)}.")

    if errs:
        raise ConfigError("Конфиг не прошёл проверку:\n  - " + "\n  - ".join(errs))
    return cfg


def critical_cv(uplift, level, n_per_group):
    """Разброс, при котором прирост ПЕРЕСТАЁТ быть отличимым от шума.

    Обратная задача к mde_mean: разброса нет, но есть прирост, уровень и размеры
    групп — и этого хватает, чтобы спросить не «доказан ли результат», а «насколько
    однородной должна быть база, чтобы он был доказан». Ответ сравнивается с типичным
    диапазоном рознице, и решение становится проверяемым без единой новой цифры.
    """
    if not n_per_group or level <= 0 or uplift <= 0:
        return None
    return uplift * math.sqrt(n_per_group) / (4.0 * level)


def breakeven_decay(cfg, lo=1.0, hi=None, steps=24):
    """Срок жизни эффекта, при котором расчёт выходит в ноль по NPV.

    Когда срока никто не знает — а это обычное дело, замер всегда короче горизонта —
    спорить о его величине бессмысленно. Полезнее перевернуть вопрос: при каком сроке
    решение меняется. «Окупается, если эффект держится дольше 11 месяцев» — утверждение,
    которое можно проверить качественно, не имея ни одной новой цифры.

    Возвращает None, если знак NPV не меняется на всём диапазоне: тогда срок ни на что
    не влияет, и это ответ лучше любого числа — решение от него не зависит.
    """
    if not isinstance(cfg.get("base_activation"), dict):
        return None
    hi = hi or float(g(cfg.get("meta") or {}, "horizon_months", 36))
    if hi <= lo:
        return None

    def npv_at(d):
        c = copy.deepcopy(cfg)
        c["base_activation"]["decay_months"] = d
        return evaluate(c)[1]["npv"]

    n_lo, n_hi = npv_at(lo), npv_at(0.0)      # 0 = не затухает, самый щедрый вариант
    if (n_lo >= 0) == (n_hi >= 0):
        return None
    a, b = lo, hi
    for _ in range(steps):
        m = (a + b) / 2.0
        if (npv_at(m) >= 0) == (n_lo >= 0):
            a = m
        else:
            b = m
    return round((a + b) / 2.0, 1)


def decay_from_tail(window_months, tail_months, ratio):
    """Срок затухания из ДВУХ чисел: средней за окно замера и средней за его хвост.

    Помесячной аналитики обычно нет, а два среза за разные периоды достаются тем же
    запросом, что и одна средняя. Этого достаточно: если хвост равен средней, эффект
    вышел на полку; если ниже — видно и факт затухания, и его скорость.

    Выведено ТОЧНО для той модели, которую применяет `decay_curve`: линейное падение
    до нуля за D месяцев, вес когорты в месяц k равен 1 − k/D. Средняя за первые W
    месяцев тогда 1 − (W−1)/2D, средняя за последние T месяцев окна 1 − (2W−T−1)/2D,
    и их отношение однозначно задаёт D. Подгонять другую форму под чужую кривую
    нельзя: получится срок, при котором движок посчитает не то, что мы оценили.

    Возвращает None, когда затухания не видно (хвост не ниже средней) — это не ошибка,
    а результат: эффект держится, и срок из таких данных не выводится.
    """
    W, T = window_months, tail_months
    if W <= 1 or not (0 < T < W) or ratio is None or ratio <= 0 or ratio >= 1:
        return None
    a = (W - 1) / 2.0                 # средний возраст наблюдения по всему окну
    b = (2 * W - T - 1) / 2.0         # то же по хвосту
    d = (b - ratio * a) / (1.0 - ratio)
    return d if d > 0 else None


def mde_mean(sd, n_per_group):
    """То же для НЕПРЕРЫВНОЙ величины: остатка, оборота, чека.

    Обратная к n ≈ 16σ²/d² — та же α=0,05 и мощность 80%, что и у долей, только
    разброс здесь не выводится из самой величины. У доли σ² = p(1−p) известно по p,
    и хватает одного числа. У рубля разброс независим: два пилота с одинаковым
    средним оборотом доказывают разное, если в одном клиенты однородны, а в другом
    среднее держат несколько крупных.

    Поэтому «даны только средние» — это не мелкий пробел в данных, а отсутствие
    половины входа. Считать доказательность нечем, и делать вид, что есть, нельзя.
    """
    if not n_per_group or sd <= 0:
        return None
    return 4.0 * sd / math.sqrt(n_per_group)


def mde(rate, n_per_group):
    """Минимальный эффект, который выборка такого размера способна отличить от шума.

    Обратная к n ≈ 16·p·(1−p)/d² при α=0,05 и мощности 80%. Нужна, чтобы задать
    прошедшему пилоту вопрос, который ему обычно не задают: а мог ли он вообще
    доказать то, что докладывает? Пилот на восемьсот человек не ловит два пункта
    конверсии, и «показал эффективность» там означает «мы что-то увидели».
    """
    if not n_per_group or rate <= 0 or rate >= 1:
        return None
    return math.sqrt(16.0 * rate * (1 - rate) / n_per_group)


def apply_pilot(cfg):
    """Пилот как ИСТОЧНИК ДАННЫХ: переносит факт в параметры и записывает, что сделал.

    Переопределение параметров — ровно тот класс тихих ошибок, который движок ловит
    у других. Разница в том, что здесь каждая подстановка попадает в отчёт строкой:
    что заменено, на что, с каким коэффициентом переноса и каким грейдом.
    """
    p = cfg.get("pilot")
    if not p:
        return cfg
    cfg = copy.deepcopy(cfg)
    # ПОСЛЕ копии: иначе `p` продолжает указывать на исходный конфиг, и всё, что
    # функция допишет в пилот, уедет во входной объект, а не в результат.
    p = cfg["pilot"]
    notes, applied = [], []
    label = p.get("name") or "пилот"

    same_geo = (not p.get("rollout_geography")
                or p.get("geography") == p.get("rollout_geography"))
    haircut = g(p, "scale_haircut", 1.0)
    grade = "A" if same_geo else "B_minus"

    if not same_geo:
        notes.append(
            f"ПИЛОТ — ПЕРЕНОС ГЕОГРАФИИ: «{label}» проведён в «{p['geography']}», "
            f"а разворачивается на «{p['rollout_geography']}». Это грейд B−, а не A: "
            "тот же перенос с чужого рынка, только внутри страны. Конверсия, оборот "
            "и медиаинфляция в регионах другие — цифры пилота сюда переносятся "
            "с коэффициентом, а не как есть.")
    if haircut >= 1.0 and p.get("metrics"):
        notes.append(
            "ПИЛОТ БЕЗ КОЭФФИЦИЕНТА ПЕРЕНОСА: scale_haircut = 1.0, то есть результат "
            "пилота переносится на масштаб целиком. Так почти никогда не бывает: пилот "
            "катят на лояльной базе, в лучшей географии и с ручным вниманием команды. "
            "Задайте scale_haircut явно — это допущение, а не отсутствие допущения.")

    # Коэффициент переноса режет ПРИРОСТ, который обещает пилот, а не абсолютный уровень.
    # Иначе выходит абсурд: пилот дал конверсию 61% при заложенных в модель 55%,
    # осторожный коэффициент 0,75 превращает это в 45,75% — то есть чем аккуратнее
    # мы переносим хорошую новость, тем хуже становится база, которая была и без пилота.
    # Правильно: 55% + (61% − 55%) × 0,75 = 59,5%. Осторожность применяется к дельте.
    # У поведенческого пилота контрольная группа — это строка с ролью «контроль».
    # Отдельная галочка рядом с таблицей позволяла бы противоречить самому себе:
    # галочка снята, строка контроля есть.
    if any(gr.get("role") == "control" for gr in (p.get("groups") or [])):
        p["control_group"] = True

    # --- замеры по группам → разница разниц ------------------------------------
    derived_out = {}
    groups = p.get("groups") or []
    if groups:
        by_metric = {}
        for gr in groups:
            by_metric.setdefault(gr.get("metric"), []).append(gr)
        derived = {}
        for metric, rows_g in sorted(by_metric.items()):
            tests = [r for r in rows_g if r.get("role") == "test"]
            ctrls = [r for r in rows_g if r.get("role") == "control"]
            if not tests:
                continue
            d_test = sum(g(r, "value_end") - g(r, "value_start") for r in tests) / len(tests)
            if not ctrls:
                notes.append(
                    f"РОСТ БЕЗ КОНТРОЛЯ — НЕ ЭФФЕКТ: {metric} вырос на {nf(d_test)} "
                    "внутри группы за время пилота, но контрольной группы в замерах нет. "
                    "За тот же срок клиенты стали старше, прошёл сезон, изменился рынок — "
                    "всё это сидит в этой цифре. Добавьте строку с ролью «контроль»: "
                    "эффект это разница разниц, а не разница до и после.")
                continue
            d_ctrl = sum(g(r, "value_end") - g(r, "value_start") for r in ctrls) / len(ctrls)
            did = d_test - d_ctrl
            gap0 = (sum(g(r, "value_start") for r in tests) / len(tests)
                    - sum(g(r, "value_start") for r in ctrls) / len(ctrls))
            derived[metric] = did
            derived_out[metric] = {"test": round(d_test, 2), "control": round(d_ctrl, 2),
                                   "effect": round(did, 2), "gap_at_start": round(gap0, 2)}
            notes.append(
                f"РАЗНИЦА РАЗНИЦ: {metric} — тест {nf(d_test)}, контроль {nf(d_ctrl)}, "
                f"эффект {nf(did)}. Контроль прожил тот же срок, поэтому сезон, взросление "
                "клиентов и движение рынка из цифры уже вычтены.")
            base_lvl = sum(g(r, "value_start") for r in ctrls) / len(ctrls)
            if base_lvl and abs(gap0) > 0.05 * abs(base_lvl):
                notes.append(
                    f"ГРУППЫ РАЗОШЛИСЬ НА СТАРТЕ: {metric} — разрыв {nf(gap0)} ещё до "
                    "пилота, то есть выравнивание сработало не полностью. Разница разниц "
                    "это учитывает (стартовый разрыв вычитается), но чем он больше, "
                    "тем сильнее допущение, что дальше группы двигались бы одинаково.")
            elif base_lvl:
                notes.append(
                    f"ВЫРАВНИВАНИЕ ПОДТВЕРЖДЕНО ЗАМЕРОМ: {metric} — на старте группы "
                    f"расходились на {nf(gap0)} при уровне {nf(base_lvl)}. Это не заявление "
                    "о дизайне, а проверка по вашим же числам.")
            notes.append(
                f"ЭТО УРОВЕНЬ НА КОНЕЦ ОКНА: {metric} — эффект измерен в последний месяц "
                "замера, а не усреднён по всему сроку. Если группы выровняли, на старте "
                "разницы не было и все восемь месяцев она нарастала: средняя за окно "
                "тогда ЗАНИЖАЕТ полку, а конец окна — лучшая её оценка из имеющегося.")
        for metric, v in derived.items():
            if metric not in (p.get("metrics") or {}):
                p.setdefault("metrics", {})[metric] = v
        if derived:
            # посчитанное из абсолютов — однозначно прирост против контроля,
            # спрашивать «уровень или прирост» здесь уже нечего
            p["metrics_are"] = "uplift"

    metrics_are = p.get("metrics_are")
    skipped = []
    for k, v in (p.get("metrics") or {}).items():
        sec = PILOT_TARGET.get(k)
        if not sec:
            continue
        # Прирост против контроля — не уровень. Пересчитать его в эффект умеет
        # растранзачивание базы (turnover_before/after), а подстановка в юнит-экономику
        # даст клиента с оборотом в размер прироста. Молча этого не делаем.
        if metrics_are == "uplift" and k in PILOT_LEVEL_METRICS:
            # Блок «что дал пилот» обязан показывать и то, чего он не дал. Иначе
            # человек видит короткий список перенесённого и считает, что это всё,
            # что пилот измерил, — а самая денежная метрика тихо осталась за кадром.
            skipped.append({"param": f"{sec}.{k}", "pilot_value": v,
                            "why": "прирост против контроля, а параметр модели — "
                                   "уровень: считается растранзачиванием базы"})
            notes.append(
                f"ПРИРОСТ НЕ ПОДСТАВЛЕН КАК УРОВЕНЬ: {sec}.{k} = {v:.6g} объявлен приростом "
                "против контрольной группы, а этот параметр модели — абсолютный уровень. "
                "Подстановка сделала бы клиента, у которого весь оборот равен приросту. "
                "Поведенческий прирост считается блоком «Растранзачивание своей базы»: "
                "задайте «до» и «после» там — он умножит разницу на число клиентов "
                "раскатки и переведёт в деньги по ставкам продукта.")
            continue
        was = cfg.setdefault(sec, {}).get(k)
        has_base = isinstance(was, (int, float)) and not isinstance(was, bool)
        new = (was + (v - was) * haircut) if has_base else v * haircut
        cfg[sec][k] = new
        applied.append({"param": f"{sec}.{k}", "pilot_value": v,
                        "applied_value": new, "previous": was if has_base else None,
                        "grade": grade})
        if metrics_are == "level" and k in PILOT_LEVEL_METRICS and p.get("control_group"):
            notes.append(
                f"УРОВЕНЬ, А НЕ ПРИРОСТ: {sec}.{k} взят как абсолютная величина тестовой "
                "группы — так и объявлено. Контрольная группа для этой метрики "
                "не использовалась: она отвечает на вопрос «взяли бы и без нас», "
                "а не «сколько тратит клиент продукта».")
        notes.append(
            f"ИЗ ПИЛОТА: {sec}.{k} = {new:.6g} — "
            + (f"было {was:.6g}, пилот даёт {v:.6g}, перенесли {haircut:.0%} прироста"
               if has_base else
               f"факт пилота {v:.6g} × коэффициент переноса {haircut:g}, базы в конфиге не было")
            + f". Грейд {grade}.")

    # Доказательность: контрольная группа и мощность выборки.
    obs, base = g(p, "observed_rate"), g(p, "baseline_rate")
    uplift = obs - base if (obs and base) else None
    power = None
    if not p.get("control_group"):
        notes.append(
            f"ПИЛОТ БЕЗ КОНТРОЛЬНОЙ ГРУППЫ: «{label}» измерял результат без контроля. "
            "«Показал эффективность» в таком дизайне означает «мы что-то сделали, "
            "и что-то произошло»: отделить эффект инициативы от сезона, органики "
            "и параллельных активностей нечем. Все цифры из этого пилота — грейд C, "
            "а не A, каким бы своим ни был источник.")
    elif uplift is not None:
        n = min(g(p, "sample_size"), g(p, "control_size")) or g(p, "sample_size")
        d_min = mde(base, n)
        if d_min:
            power = {"observed_uplift": round(uplift, 4), "min_detectable": round(d_min, 4),
                     "n_per_group": round(n), "conclusive": uplift >= d_min}
            if uplift < d_min:
                notes.append(
                    f"ПИЛОТ НИЧЕГО НЕ ДОКАЗАЛ: наблюдаемый прирост {uplift:.1%} меньше "
                    f"минимального, который эта выборка способна отличить от шума "
                    f"({d_min:.1%} при {nf(n)} на группу). Результат статистически "
                    "неотличим от нуля — на нём нельзя строить решение о масштабировании. "
                    f"Чтобы поймать {uplift:.1%}, нужно около "
                    f"{nf(16 * base * (1 - base) / uplift ** 2)} на группу.")
            else:
                notes.append(f"ПИЛОТ ДОКАЗАТЕЛЕН: прирост {uplift:.1%} против минимально "
                             f"различимого {d_min:.1%} при {nf(n)} на группу.")
    # --- дизайн: рандомизация снимает отбор, её отсутствие — нет ---------------
    if p.get("control_group"):
        if p.get("randomized"):
            notes.append(
                "ДИЗАЙН: группы рандомизированы"
                + (" и выровнены на старте" if p.get("matched_at_baseline") else "")
                + ". Разница между группами объясняется продуктом, а не тем, кто "
                  "в какую группу попал: поправка на отбор не нужна.")
        else:
            notes.append(
                "ОТБОР ВМЕСТО ЭФФЕКТА: контрольная группа есть, но рандомизация "
                "не заявлена. Если клиенты сами выбрали продукт, они и до него были "
                "активнее — часть разницы объясняется отбором, а не продуктом, и на "
                "масштабе её не будет. Задайте randomized, если деление было случайным; "
                "иначе результат нужно резать поправкой на отбор.")

    # --- доказательность непрерывных метрик ------------------------------------
    lvl_uplifts = {k: v for k, v in (p.get("metrics") or {}).items()
                   if k in PILOT_LEVEL_METRICS} if p.get("metrics_are") == "uplift" else {}
    if lvl_uplifts and p.get("control_group"):
        n_c = min(g(p, "sample_size"), g(p, "control_size")) or g(p, "sample_size")
        cv = g(p, "metric_cv")
        for k, v in sorted(lvl_uplifts.items()):
            if cv <= 0:
                lvl0 = g(cfg.get("unit_economics") or {}, k)
                ccv = critical_cv(v, lvl0, n_c) if lvl0 > 0 else None
                if ccv is None:
                    notes.append(
                        f"ДОКАЗАТЕЛЬНОСТЬ НЕ ПРОВЕРЕНА: {k} — прирост {nf(v)} при "
                        f"{nf(n_c)} на группу, но разброса нет, а у рубля он из среднего "
                        "не выводится. Задайте metric_cv (обычно 1,5–3 на рознице).")
                    continue
                # Разброса нет и не будет — тогда спрашиваем не «доказано ли»,
                # а «насколько однородной должна быть база, чтобы было доказано».
                # Ответ считается из того, что уже есть, и его можно проверить
                # качественно: это утверждение о базе, а не о пилоте.
                verdict = ("результат устойчив: столь однородной базы на рознице "
                           "не бывает даже близко" if ccv >= 4.0 else
                           "результат на грани — типичный разброс на рознице 1,5–3×, "
                           "и он накрывает эту границу" if ccv >= 1.5 else
                           "результат хрупкий: чтобы прирост был различим, база должна "
                           "быть однороднее, чем на рознице бывает")
                notes.append(
                    f"РАЗБРОСА НЕТ — СЧИТАЕМ ОБРАТНОЕ: {k}, прирост {nf(v)} при "
                    f"{nf(n_c)} на группу различим, пока разброс не выше "
                    f"{ccv:.1f}× от среднего. {verdict[0].upper() + verdict[1:]}. "
                    "Это не замена замеру, но проверить такое утверждение можно "
                    "и без новых цифр.")
                continue
            base_lvl = g(cfg.get("unit_economics") or {}, k)
            sd = cv * (base_lvl if base_lvl > 0 else abs(v))
            d_min = mde_mean(sd, n_c)
            if d_min is None:
                continue
            if v < d_min:
                notes.append(
                    f"ПРИРОСТ НЕОТЛИЧИМ ОТ ШУМА: {k} — {nf(v)} при минимально различимом "
                    f"{nf(d_min)} (разброс {cv:g}× от среднего, {nf(n_c)} на группу). "
                    "На таком результате нельзя строить решение о масштабировании.")
            else:
                notes.append(
                    f"ПРИРОСТ ДОКАЗАТЕЛЕН: {k} — {nf(v)} против минимально различимого "
                    f"{nf(d_min)} при {nf(n_c)} на группу.")

    # --- затухание из хвоста окна ---------------------------------------------
    # Два числа вместо восьми: помесячной аналитики обычно нет, а два среза за разные
    # периоды достаются тем же запросом, что и одна средняя.
    tail_vals = p.get("metrics_tail") or {}
    decay_est = {}
    if tail_vals:
        W, T = g(p, "duration_months"), g(p, "tail_months")
        for k, tail_v in sorted(tail_vals.items()):
            avg_v = g(p.get("metrics") or {}, k)
            if not avg_v:
                continue
            r = tail_v / avg_v
            if r >= 1.0:
                notes.append(
                    f"ЭФФЕКТ НЕ ЗАТУХАЕТ: {k} — за последние {T:g} мес окна {nf(tail_v)} "
                    f"против средней за {W:g} мес {nf(avg_v)}. Эффект вышел на полку "
                    "или ещё растёт, и перенос средней на горизонт скорее ЗАНИЖАЕТ. "
                    "Затухание можно не задавать — но это теперь замер, а не догадка.")
                continue
            d = decay_from_tail(W, T, r)
            if d is None:
                continue
            decay_est[k] = round(d, 1)
            notes.append(
                f"ЗАТУХАНИЕ ИЗ ЗАМЕРА: {k} — хвост {nf(tail_v)} против средней "
                f"{nf(avg_v)} даёт {r:.0%}, то есть эффект гаснет примерно за "
                f"{d:.0f} мес от выдачи. Ставьте decay_months = {d:.0f} там, где "
                "считается эффект: это выведено из вашего же замера, а не взято "
                "с потолка.")
        if decay_est:
            notes.append(
                "СРЕДНЯЯ ЗА ОКНО — НЕ ПИК: раз эффект затухает, средняя за весь замер "
                "ниже первого месяца и выше последнего. Движок применяет затухание "
                "к тому уровню, который вы задали как «после», поэтому подставленная "
                "средняя занижает начало и завышает хвост. Точнее — брать уровень "
                "первых месяцев, если он есть.")

    # --- окно наблюдения против горизонта расчёта ------------------------------
    dur = g(p, "duration_months")
    hor = g(cfg.get("meta") or {}, "horizon_months")
    if dur > 0 and p.get("metrics"):
        if 0 < dur < 12:
            notes.append(
                f"ОКНО НАБЛЮДЕНИЯ {dur:g} МЕС ИЗ ДВЕНАДЦАТИ: остальные месяцы года "
                "не наблюдались, и результат переносится на них как на наблюдавшиеся. "
                "Если эффект сезонный — а на молодёжных продуктах лето и декабрь "
                "не похожи на остальной год — годовая цифра смещена ровно на эту разницу. "
                "Контрольная группа снимает сезон ВНУТРИ окна, но не достраивает "
                "то, чего не мерили.")
        if hor > dur:
            notes.append(
                f"ЗАМЕР {dur:g} МЕС, ГОРИЗОНТ {hor:g} МЕС: разница держится дальше срока "
                "замера по допущению, а не по факту. Средняя за окно смешивает всплеск "
                "первых месяцев и полку — если эффект затухает, перенос средней на весь "
                "горизонт завышает. Задайте затухание явно там, где считается эффект.")

    if p.get("audience"):
        notes.append(f"ОТБОР В ПИЛОТЕ: аудитория «{p['audience']}». Если она отличается "
                     "от аудитории раскатки, разница в конверсии объясняется отбором, "
                     "а не продуктом. На холодной аудитории результат будет ниже.")

    cfg["_pilot"] = {"decay_from_tail": decay_est or None, "skipped": skipped,
                     "derived": derived_out or None,
                     "name": label, "grade": grade, "scale_haircut": haircut,
                     "same_geography": same_geo, "control_group": bool(p.get("control_group")),
                     "applied": applied, "power": power, "uplift": uplift}
    cfg.setdefault("_notes", []).extend(notes)
    return cfg


def brief_coverage(cfg):
    """Что из сказанного человеком дошло до модели, а что осталось за бортом."""
    b = cfg.get("brief")
    if not b:
        return None
    claims = b.get("claims") or []
    by = {s: [c for c in claims if c.get("status") == s] for s in CLAIM_STATUS}
    return {"total": len(claims),
            "applied": len(by["applied"]), "questions": len(by["question"]),
            "ignored": len(by["ignored"]),
            "coverage": round(len(by["applied"]) / len(claims), 2) if claims else None,
            "open_questions": [{"claim": c["claim"], "implication": c.get("implication")}
                               for c in by["question"]],
            "not_counted": [{"claim": c["claim"], "reason": c.get("reason")}
                            for c in by["ignored"]],
            "parameters": [{"claim": c["claim"], "maps_to": c["maps_to"],
                            "grade": c.get("grade")} for c in by["applied"]]}


def normalize(cfg):
    """Производные вводные, которые нельзя задавать независимо друг от друга.

    Средний баланс и месячный оборот — не два свободных параметра: остаток на карте
    это доля прошедших через неё денег. Заданные порознь, они легко противоречат
    друг другу (оборот 30 тыс при остатке 300 тыс), и модель считает без ошибок.

    Выполняется ДО сведения сегментов и применяется к КАЖДОМУ сегменту от его
    собственного оборота. Если вывести баланс один раз глобально и подставить после
    взвешивания, ломается точность, ради которой веса и подобраны: ставки взвешены
    по базе, к которой применяются, и `баланс_средн × NIM_средн` обязан совпадать
    с посегментной суммой ТОЧНО. Глобальная подстановка расходилась с книгой
    на 28 млн ₽ NPV — и это ловилось только сверкой с Excel.
    """
    u = cfg.get("unit_economics") or {}
    ratio = g(u, "balance_to_turnover")
    if ratio <= 0:
        return cfg
    notes = []

    def derive(block, where):
        turn = block.get("monthly_turnover")
        if turn is None:
            return
        derived = turn * ratio
        was = block.get("avg_balance")
        block["avg_balance"] = derived
        tail = (f" (заданное {nf(was)} перекрыто)"
                if isinstance(was, (int, float)) and abs(was - derived) > 1 else "")
        notes.append(f"ПРОИЗВОДНАЯ ВЕЛИЧИНА: {where} баланс = {ratio:.0%} от оборота "
                     f"{nf(turn)} → {nf(derived)}{tail}.")

    segs = (cfg.get("audience") or {}).get("segments") or []
    for s in segs:
        if isinstance(s, dict):
            derive(s, f"сегмент «{s.get('id')}»:")
    derive(u, "общий:")
    cfg.setdefault("_notes", []).extend(notes)
    return cfg


def prepare(cfg):
    """Единственная сборка конфига к расчёту: проверка → пилот → производные → сегменты.

    Была продублирована: CLI собирал так, а веб — `resolve_segments(validate_config(cfg))`,
    без пилота и без normalize. То есть в приложении пилот НЕ ПРИМЕНЯЛСЯ ВОВСЕ: блок
    «что дал пилот» на экране результата не мог появиться в принципе, а баланс не
    выводился из оборота. Тесты этого не видели, потому что дёргали `apply_pilot`
    напрямую, минуя тот путь, которым ходит человек.

    Порядок значим: normalize обязан идти между пилотом и сегментами. Пилот меняет
    оборот, из оборота выводится баланс, и только потом сегменты сводятся в один
    набор. Перестановка normalize в конец однажды дала расхождение с книгой 28 млн ₽.
    """
    return resolve_segments(normalize(apply_pilot(validate_config(cfg))))


def load_config(src):
    """Читает кейс, проверяет по схеме и готовит к расчёту. Единственная точка входа.

    Порядок обязателен и проверен сверкой с Excel:
      validate  — до всего, чтобы дальше работать с заведомо корректным конфигом;
      pilot     — подставляет свой факт в общие funnel и unit_economics, чтобы сегменты
                  без собственных значений его унаследовали, а сегменты со своими перебили;
      normalize — выводит производный баланс от оборота ПОСЕГМЕНТНО, до взвешивания;
      segments  — сводит всё в один взвешенный набор допущений.

    normalize обязан идти между пилотом и сегментами. Раньше он стоял последним и
    перетирал взвешенный баланс одним глобальным числом — расхождение с книгой 28 млн ₽.
    """
    cfg = json.load(open(src, encoding="utf-8")) if src else json.load(sys.stdin)
    return prepare(cfg)


def split_fee(split, fallback):
    """Средневзвешенная ставка комиссии по типам операций.

    Одна усреднённая ставка interchange прячет то, что POS, e-POS и снятие
    оплачиваются по-разному и их доли двигаются независимо: рост доли онлайна
    меняет доход при неизменном обороте.
    """
    if not split:
        return fallback
    return sum(g(p, "share") * g(p, "fee") for p in split.values())


def is_plan_driven(cfg):
    """Объём задан планом продаж, а не медиаворонкой."""
    return g(cfg.get("acquisition") or {}, "year1_target") > 0


def acquisition_plan(cfg, horizon):
    """Абсолютный план притока по месяцам. Только для режима «объём из плана продаж»."""
    a = cfg.get("acquisition") or {}
    s = list(a.get("seasonality") or [1.0] * 12)
    tot = sum(s) or 1.0
    target, yoy = g(a, "year1_target"), g(a, "yoy_growth", 1.0)
    return [target * (yoy ** (m // 12)) * s[m % 12] / tot for m in range(horizon)]


def acquisition_shape(cfg, horizon, camp_months):
    """ДОЛИ притока новых клиентов по месяцам: сколько от общего инкремента приходит когда.

    Равномерный приток `инкремент / месяцы кампании` — удобное, но неверное
    допущение: план продаж всегда имеет сезонность и темп год-к-году. На NPV это
    влияет прямо, потому что ранние когорты живут дольше и дисконтируются меньше.

    Три режима, по убыванию точности:
      1. цель первого года + темп год-к-году + сезонный профиль — план как он есть;
      2. только сезонный профиль — форма плана без уровня, растянутая на кампанию;
      3. ничего — равномерно по месяцам кампании (прежнее поведение).

    Доли намеренно НЕ перенормируются к единице, если горизонт короче кампании:
    часть притока в таком конфиге действительно не попадает в расчёт, и молча
    дорисовывать её значит прятать ошибку ввода, которую ловит validate().
    """
    a = cfg.get("acquisition") or {}
    seas = a.get("seasonality")
    if g(a, "year1_target") > 0:
        plan = acquisition_plan(cfg, horizon)
        tot = sum(plan) or 1.0
        return [x / tot for x in plan]
    if seas:
        n = min(camp_months, horizon)
        win = [seas[m % 12] for m in range(n)]
        tot = sum(win) or 1.0
        return [win[m] / tot if m < n else 0.0 for m in range(horizon)]
    return [1.0 / camp_months if m < camp_months else 0.0 for m in range(horizon)]


def companion_stream(cfg, active, u):
    """Продукт-спутник: копилка, накопительный счёт, подписка — своя воронка на той же базе.

    Считается не отдельной инициативой, а надстройкой над активной базой основного
    продукта: attach → фондирование → баланс → маржа. Часть накопленного возвращается
    в оборот основного продукта и приносит комиссию ещё раз.
    """
    c = cfg.get("companion")
    if not c:
        return None
    attach, funded = g(c, "attach_rate"), g(c, "funded_rate", 1.0)
    bal0, nim = g(c, "avg_balance"), g(c, "nim_annual")
    growth, spend_share = g(c, "balance_growth_m"), g(c, "spend_share")
    fee = split_fee(g(u, "turnover_split", None), g(u, "interchange_rate"))
    back = bool(c.get("spend_returns_to_turnover"))
    rows = []
    for m, act in enumerate(active):
        holders = act * attach * funded
        total_bal = holders * bal0 * ((1 + growth) ** m)
        spend = total_bal * spend_share
        rows.append({"month": m + 1, "holders": holders, "balance_total": total_bal,
                     "margin": total_bal * nim / 12.0,
                     "spend": spend, "spend_fee": spend * fee if back else 0.0})
    return rows


def spread_stock_flow(stock_total, flow_monthly, workout, horizon, shape):
    """Разложить сток и поток по месяцам. Общий скелет для всех блоков по своей базе.

    Сток конечен: прорабатывается за `workout` месяцев и кончается. Ноль — растянуть
    по форме кампании. Поток идёт ровно каждый месяц и не кончается никогда.
    Возвращает две помесячные строки порознь: их вклады надо показывать раздельно,
    иначе разовая проработка базы выдаётся за постоянный сервис.
    """
    w = int(workout)
    if w > 0:
        stock = [stock_total / w if m < min(w, horizon) else 0.0 for m in range(horizon)]
    else:
        stock = [stock_total * x for x in shape]
    return stock, [flow_monthly] * horizon


def decay_curve(cohorts, decay, month):
    """Сколько из ранее набранных когорт ещё «живо» в данном месяце.

    Линейное затухание за `decay` месяцев. Ноль означает эффект без затухания —
    так почти не бывает, и validate() об этом скажет.
    """
    return sum(c * (1.0 if decay <= 0 else max(0.0, 1.0 - (month - j) / decay))
               for j, c in enumerate(cohorts[:month + 1]))


def natural_effect_stream(cfg, shape, horizon):
    """Эффект, который меряется НЕ в деньгах, и его перевод в P&L банка.

    Инициативы сплошь и рядом меряются в своей натуральной единице: снижение
    просроченной задолженности в рублях долга, сокращение обращений в колл-центр
    в штуках, экономия времени в часах. Ни одна из этих величин не равна деньгам
    банка, и подстановка её в итог завышает эффект в разы.

    На живом ФЭО AI-агента по просрочке «снижение просрочки на 3,65 млрд ₽» стояло
    в итоговой строке как эффект. Реальные деньги — это высвобожденный резерв
    (727 млн) минус недополученный процент по реструктуризации и минус отсрочка,
    выданная тем, кто заплатил бы сам. После перевода осталось 459 млн, то есть
    в восемь раз меньше, а после затрат и дисконтирования — 61 млн, в шестьдесят.

    Отсюда обязательные части блока:
      monetization — сколько КОПЕЕК P&L приносит единица натурального эффекта;
      adjustments  — именованные вычеты: инструмент помощи почти всегда чего-то стоит;
      parts        — база неоднородна, и в глубоких корзинах эффект структурно
                     невозможен: 90+ дней диалогом не лечится ни при каком агенте.
    """
    ne = cfg.get("natural_effect")
    if not ne:
        return None
    uplift = g(ne, "uplift_rate")
    parts = ne.get("parts") or []

    def _affected(base):
        """Натуральный эффект с учётом неоднородности базы."""
        if not parts:
            return base * uplift, g(ne, "monetization", 1.0)
        total, money = 0.0, 0.0
        for p in parts:
            u_p = min(uplift, g(p, "uplift_cap", 1.0))
            amt = base * g(p, "share") * u_p
            total += amt
            money += amt * g(p, "monetization", 1.0)
        return total, (money / total if total else 0.0)

    stock_nat, mon_rate = _affected(g(ne, "baseline"))
    flow_nat, _ = _affected(g(ne, "monthly_inflow"))
    if stock_nat <= 0 and flow_nat <= 0:
        return None

    adj = sum(g(a, "rate") for a in (ne.get("adjustments") or []))
    net_rate = (mon_rate + adj) * (1 - g(ne, "relapse_share"))

    stock_c, flow_c = spread_stock_flow(stock_nat, flow_nat,
                                        g(ne, "workout_months"), horizon, shape)
    rows = [{"natural": s + f, "money": (s + f) * net_rate,
             "from_stock": s * net_rate, "from_flow": f * net_rate}
            for s, f in zip(stock_c, flow_c)]
    return {"rows": rows, "unit": ne.get("unit", "ед."),
            "natural_total": sum(stock_c) + sum(flow_c),
            "natural_from_stock": sum(stock_c), "natural_from_flow": sum(flow_c),
            "monetization_rate": mon_rate, "adjustments_rate": adj,
            "net_rate": net_rate,
            "stock_exhausted_month": (int(g(ne, "workout_months"))
                                      if g(ne, "workout_months") > 0 else None),
            "money_total": sum(r["money"] for r in rows)}


def base_activation_stream(cfg, shape, u):
    """Растранзачивание своей базы: ТОТ ЖЕ человек, ТОТ ЖЕ продукт, другое поведение.

    Третья сущность эффекта, и её нельзя свести к двум существующим:
      linked_client — другой человек (родитель платит за детскую карту);
      companion     — другой продукт (копилка поверх карты);
      здесь         — тот же клиент того же продукта начинает платить активнее.

    Почему без неё нельзя считать зрелый рынок. При проникновении 89% «привлечение»
    новых людей физически упирается в потолок: их почти не осталось. Весь эффект
    инициативы сидит в том, что спящий клиент просыпается, а этого движок не умел
    выражать вовсе — воронка привлечения такой прирост не описывает.

    Единица эффекта здесь не клиент, а ПРИРОСТ ОБОРОТА И ОСТАТКА. Затрат на
    привлечение нет: клиент уже есть, платить за него второй раз не нужно.

    Растранзаченные НЕ добавляются в АКБ и НЕ попадают в инкремент: они уже в базе.
    Иначе один и тот же человек посчитался бы дважды — как новый и как проснувшийся.
    """
    b = cfg.get("base_activation")
    if not b:
        return None
    # СТОК и ПОТОК — разные вещи, и складывать их в одну строку «эффект в месяц»
    # нельзя. Накопленная база конечна: её прорабатывают за workout_months и она
    # кончается. Поток пополняется каждый месяц и работает всегда.
    #
    # На живом ФЭО это перевернуло картину: накопленный отток в 1,97 млн человек
    # выглядел источником 2 206 спасений ЕЖЕМЕСЯЧНО, хотя при таком темпе базы
    # хватило бы на 892 месяца. Реально это 2 206 один раз плюс 112 в месяц —
    # инициатива про постоянный сервис, а не про разовую проработку базы.
    # Техническая достижимость — НЕ конверсия. «Есть приложение и не в стоп-листе» —
    # физическая возможность доставить сообщение; «дотянулись» — сколько из доступных
    # реально охватили. Слитые в один множитель, они прячут, где именно теряется база:
    # 70% в живом ФЭО были достижимостью, а не откликом, и лечатся они разным.
    tech = g(b, "technically_reachable", 1.0)
    attr = g(b, "attribution_share", 1.0)
    reach = g(b, "reach_share")
    uplift = g(b, "uplift_rate")
    parts = b.get("parts") or []

    def _affected(base):
        """Сколько клиентов сменит поведение. База неоднородна: спящий месяц
        и спящий год откликаются по-разному, и в глубокой части эффект близок к нулю."""
        if not parts:
            return base * tech * reach * uplift * attr
        return sum(base * g(p, "share") * tech * reach
                   * min(uplift, g(p, "uplift_cap", 1.0)) * attr for p in parts)

    stock_total = _affected(g(b, "eligible_clients"))
    flow_monthly = _affected(g(b, "monthly_inflow"))
    if stock_total <= 0 and flow_monthly <= 0:
        return None
    # сквозная конверсия «на одного человека базы» — для экономики контакта
    base_ref = g(b, "eligible_clients") or g(b, "monthly_inflow")
    conv = ((stock_total or flow_monthly) / base_ref) if base_ref else 0.0

    d_turn = g(b, "turnover_after") - g(b, "turnover_before")
    d_bal = g(b, "balance_after") - g(b, "balance_before")
    fee = split_fee(g(u, "turnover_split", None), g(u, "interchange_rate"))
    # Ставки берутся у ОСНОВНОГО продукта: это его оборот и его остаток выросли.
    # Обслуживание не добавляется — клиента уже обслуживают, инициатива этого не меняет.
    per_client = (d_bal * g(u, "nim_annual") / 12.0
                  + d_turn * fee
                  - d_turn * g(u, "cashback_rate"))

    decay = g(b, "decay_months")
    horizon = len(shape)
    # Сток прорабатывается равномерно за workout_months и КОНЧАЕТСЯ; по умолчанию
    # растягивается по форме кампании. Поток идёт ровно, каждый месяц.
    workout = int(g(b, "workout_months", 0))
    stock_cohorts, flow_cohorts = spread_stock_flow(stock_total, flow_monthly,
                                                    workout, horizon, shape)
    cohorts = [s + f for s, f in zip(stock_cohorts, flow_cohorts)]

    rows = []
    for m in range(horizon):
        # Каждая когорта проснувшихся затухает линейно за decay_months.
        live_s = decay_curve(stock_cohorts, decay, m)
        live_f = decay_curve(flow_cohorts, decay, m)
        live = live_s + live_f
        rows.append({"active_uplifted": live, "margin": live * per_client,
                     "from_stock": live_s * per_client, "from_flow": live_f * per_client,
                     "turnover_uplift": live * d_turn, "balance_uplift": live * d_bal})
    return {"rows": rows, "activated_total": sum(cohorts),
            "activated_stock": sum(stock_cohorts), "activated_flow": sum(flow_cohorts),
            "stock_exhausted_month": (min(workout, horizon) if workout > 0 else None),
            "conversion": conv, "margin_per_client_m": per_client,
            "turnover_delta": d_turn, "balance_delta": d_bal}


def linked_stream(cfg, active):
    """Эффект на СВЯЗАННОГО клиента — того, кто сам продукт не берёт.

    Детская карта зарабатывает на родителе: он не уходит, возвращается если ушёл,
    и покупает больше, потому что банк узнал о нём новое. Три механики считаются
    порознь, потому что доказываются по-разному и имеют разные грейды:

      удержание   — отток связанного клиента × снижение оттока. Полностью инкрементально:
                    без инициативы этот человек ушёл бы. Проверяется контрольной группой.
      привлечение — новые и реактивированные связанные клиенты. Их ОД инкрементален
                    лишь частично, поэтому режется на attribution_share: часть пришла бы
                    и без инициативы.
      кросс-селл  — прирост ОД действующих связанных клиентов от знания о них нового.
                    Инкрементален по определению, это именно ПРИРОСТ.
    """
    l = cfg.get("linked_client")
    if not l:
        return None
    per = g(l, "clients_per_linked", 1.0) or 1.0
    inc_m = g(l, "income_annual") / 12.0
    mr = g(l, "margin_rate", 1.0)
    attr = g(l, "attribution_share", 1.0)
    src = l.get("sources") or {}
    share_acq = g(src, "new") + g(src, "reactivated") if src else 1.0
    churn_m, red = g(l, "churn_annual") / 12.0, g(l, "churn_reduction")
    sel = g(l, "selection_haircut", 1.0)
    xsell = g(l, "cross_sell_uplift")
    # Удержание ОТКЛАДЫВАЕТ уход, а не отменяет его. Раньше `retained` только рос:
    # связанный клиент, удержанный на первом месяце, приносил полный доход все
    # 36 месяцев и не уходил никогда. Это завышало блок удержания тем сильнее,
    # чем длиннее горизонт. Спасённый возвращается в обычную кривую оттока
    # и оттекает по той же ставке — иммунитета инициатива не даёт.
    retained, rows = 0.0, []
    for act in active:
        linked = act / per
        retained = retained * (1 - churn_m) + linked * churn_m * red
        acq_inc = linked * share_acq * inc_m * attr
        # ПОПРАВКА НА ОТБОР. `income_annual` — средний ОД связанного клиента, но
        # удержанный отобран по признаку: он собирался уйти, то есть по определению
        # вовлечён хуже среднего. Применять к нему средний NPV значит завышать.
        # Тот же довод, что и к NPV нового клиента, приложенному к уходящему:
        # уходящие — худшая часть базы, а не случайная выборка из неё.
        ret_inc = retained * inc_m * sel
        xs_inc = linked * inc_m * xsell
        rows.append({"linked_clients": linked, "retained_cum": retained,
                     "income_acquisition": acq_inc * mr, "income_retention": ret_inc * mr,
                     "income_cross_sell": xs_inc * mr,
                     "total": (acq_inc + ret_inc + xs_inc) * mr})
    return rows


def partner_pl(cfg, active, cohorts, u):
    """P&L партнёра на той же клиентской базе: свои доли операций и свои ставки.

    Нужен отдельным блоком, а не слитым в общий итог: у партнёра своя экономика,
    и инициатива, выгодная банку, может быть убыточной для того, без кого она
    не работает. Это выясняется на переговорах, а не после запуска.
    """
    p = cfg.get("partner")
    if not p:
        return None
    turn = g(u, "monthly_turnover")
    fee = split_fee(p.get("turnover_split"), 0.0)
    cards = sum(cohorts)
    txn_rev = sum(a * turn for a in active) * fee
    card_rev = cards * g(p, "fee_per_card")
    card_cost = cards * g(p, "cost_per_card")
    return {"name": p.get("name", "Партнёр"), "cards_issued": round(cards),
            "turnover_total": round(sum(a * turn for a in active)),
            "effective_fee": round(fee, 6),
            "revenue_transactions": round(txn_rev), "revenue_cards": round(card_rev),
            "costs": round(card_cost), "net": round(txn_rev + card_rev - card_cost)}


def poisson_reach(impressions, audience):
    """Reach(1+) = 1 - e^(-lambda), где lambda = средняя частота контакта на человека в ЦА."""
    if audience <= 0 or impressions <= 0:
        return 0.0, 0.0
    lam = impressions / audience
    reach = min(1.0 - math.exp(-lam), 0.98)      # 100% ЦА не охватывается никогда
    # Частота — среди РЕАЛЬНО охваченных, то есть по уже обрезанному охвату, как на листе
    # «Каналы». Делить на необрезанный охват значит занижать частоту там, где охват упёрся
    # в потолок, и разводить движок с книгой (18,00 против 18,37 на ЦА 20 млн).
    freq = lam / reach if reach > 0 else 0.0
    return reach, freq

def build_funnel(cfg):
    aud = g(cfg["audience"], "target_size")
    base_apps_m = g(cfg["audience"], "baseline_organic_apps_m")
    f = cfg["funnel"]
    ch_out, media_cost = [], 0.0
    direct_apps = 0.0; brand_uplift_weighted = 0.0
    reach_complement = 1.0; total_impr = 0.0

    for c in cfg.get("channels", []):
        kind = c.get("kind", "perform"); budget = g(c, "budget")
        media_cost += budget; brand_before = brand_uplift_weighted
        if kind == "internal":
            impr = g(c, "reach_abs")
            apps = impr * g(c, "response_rate") * g(c, "cr_to_app")
            r_share, freq = (min(impr / aud, 0.98) if aud else 0.0), 1.0
            direct_apps += apps
        else:
            cpm = g(c, "cpm")
            impr = (budget / cpm * 1000.0) if cpm > 0 else g(c, "impressions")
            r_share, freq = poisson_reach(impr, aud)
            if kind == "perform":
                apps = impr * g(c, "ctr") * g(c, "cr_to_app")
                direct_apps += apps
            else:  # brand — эффект через прирост органики, а не прямая атрибуция
                apps = 0.0
                eff = min(1.0, freq / g(c, "effective_freq_target", 3.0))
                brand_uplift_weighted += g(c, "brand_uplift") * r_share * eff
        total_impr += impr; reach_complement *= (1.0 - r_share)
        brand_add = brand_uplift_weighted - brand_before
        ch_out.append({"name": c.get("name", kind), "kind": kind, "budget": round(budget),
                       "impressions": round(impr), "reach_share": round(r_share, 4),
                       "freq": round(freq, 2), "applications": round(apps),
                       "brand_contribution": round(brand_add, 6),
                       "cost_per_app": round(budget / apps) if apps > 0 else None})

    combined_reach = 1.0 - reach_complement
    months = max(g(cfg["costs"], "campaign_months", 1), 1)
    halo_m = g(cfg["audience"], "halo_months", 2.0); halo_decay = g(cfg["audience"], "halo_decay", 0.5)
    brand_apps = base_apps_m * brand_uplift_weighted * (months + halo_m * halo_decay)
    total_apps = direct_apps + brand_apps
    approved = total_apps * g(f, "approval_rate", 1.0)
    activated = approved * g(f, "activation_rate", 1.0)

    # Объём может задаваться планом продаж, а не медиаворонкой — так устроен вывод
    # продукта на полку. Тогда план определяет уровень, но медиаворонка НЕ выбрасывается:
    # разрыв между «сколько даёт медиаплан» и «сколько обещает план» — самый содержательный
    # вопрос к инициативе, и validate() его задаёт вслух.
    activated_media = activated
    plan_total = None
    if is_plan_driven(cfg):
        plan_total = sum(acquisition_plan(cfg, int(g(cfg["meta"], "horizon_months", 24))))
        activated = plan_total

    incremental = activated * (1 - g(f, "cannibalization")) * (1 - g(f, "deadweight"))
    return {"channels": ch_out, "target_audience": round(aud),
            "combined_reach_share": round(combined_reach, 4),
            "combined_reach_abs": round(combined_reach * aud),
            "total_impressions": round(total_impr),
            "avg_frequency": round(total_impr / (combined_reach * aud), 2) if combined_reach * aud > 0 else 0,
            "applications_direct": round(direct_apps), "applications_brand_lift": round(brand_apps),
            "applications_total": round(total_apps), "approved": round(approved),
            "volume_source": "план продаж" if plan_total is not None else "медиаворонка",
            "activated_from_media": round(activated_media),
            "activated_from_plan": round(plan_total) if plan_total is not None else None,
            "activated": round(activated), "cannibalization_loss": round(activated - incremental),
            "incremental_clients": round(incremental),
            "_incremental_raw": incremental, "_activated_raw": activated, "_media_cost": media_cost}

def monthly_margin(u, month_index):
    """Contribution margin на одного активного клиента в месяц.

    Комиссионный доход берётся из разложения оборота по типам операций
    (POS / e-POS / снятие со своими ставками), если оно задано, иначе — из одной
    усреднённой ставки interchange_rate.
    """
    ramp = min(1.0, (month_index + 1) / max(g(u, "ramp_months", 1), 1))
    bal, turn = g(u, "avg_balance"), g(u, "monthly_turnover")
    fee = split_fee(g(u, "turnover_split", None), g(u, "interchange_rate"))
    rev = bal * g(u, "nim_annual") / 12.0 + turn * fee + g(u, "fee_income_m")
    cost = turn * g(u, "cashback_rate") + g(u, "servicing_cost_m") + bal * g(u, "cor_annual") / 12.0
    return (rev - cost) * ramp

def market_position(cfg, active):
    """Какую долю рынка обещает план — первый вопрос, который задаёт комитет.

    Движок способен насчитать три миллиона клиентов на рынке в шесть с половиной
    миллионов и не сказать ни слова. Доля считается от ДОСТИЖИМОГО рынка, а не от
    всего: разница обычно четверть и больше, и она всегда в пользу «у нас скромный план».
    """
    aud = cfg.get("audience", {})
    explicit = g(aud, "reachable_market") > 0
    reach = g(aud, "reachable_market") or g(aud, "target_size")
    total = g(aud, "market_total")
    if not reach or not active:
        return None
    peak = max(active)
    return {"reachable_market": round(reach), "market_total": round(total) if total else None,
            "basis": "достижимый рынок" if explicit else "ЦА (рынок не задан)",
            "explicit": explicit, "clients_peak": round(peak),
            "share_of_reachable": round(peak / reach, 4),
            "share_of_total": round(peak / total, 4) if total else None}


def effect_summary(u, rows, r_m, net, npv):
    """Один и тот же прирост клиентов, посчитанный в разных валютах.

    Стейкхолдеры меряют разным: маркетинг — АКБ, финансы — ОД и PL, розница — AUM
    и дополнительный объём средств. Считаем всё из одного прогона, иначе цифры
    разъедутся между презентациями и спорить будут о них, а не о решении.

    ОД — это ДОХОД, а не маржа: он не вычитает кэшбэк, обслуживание и риск. Сравнивать
    его с NPV напрямую нельзя, это разные вещи, и обе нужны.
    """
    od = g(u, "operating_income_annual")
    bal = g(u, "avg_balance")
    peak = max((x["active_clients"] for x in rows), default=0.0)
    end = rows[-1]["active_clients"] if rows else 0.0
    client_months = sum(x["active_clients"] for x in rows)
    od_total = client_months * od / 12.0
    od_disc = sum(x["active_clients"] * od / 12.0 / ((1 + r_m) ** (i + 1))
                  for i, x in enumerate(rows))
    return {
        "akb_peak": round(peak), "akb_end": round(end), "client_months": round(client_months),
        "od_annual_per_client": round(od) if od else None,
        "od_total": round(od_total) if od else None,
        "od_discounted": round(od_disc) if od else None,
        # доп. объём средств на продуктах = баланс инкрементальных клиентов
        "aum_peak": round(peak * bal) if bal else None,
        "aum_end": round(end * bal) if bal else None,
        "funds_per_client": round(bal) if bal else None,
        "pl_net": round(net), "pl_npv": round(npv),
    }


def run_pl(cfg, funnel):
    h = int(g(cfg["meta"], "horizon_months", 24))
    r_m = (1 + g(cfg["meta"], "discount_rate_annual", 0.20)) ** (1 / 12.0) - 1
    u, k = cfg["unit_economics"], cfg["costs"]
    churn = g(u, "monthly_churn"); camp_m = int(max(g(k, "campaign_months", 1), 1))
    inc = funnel["_incremental_raw"]; media = funnel["_media_cost"]
    # ПЕРЕМЕННЫЕ затраты на привлечение (реферальные выплаты, промокоды, бонусы за открытие).
    # Платятся за КАЖДОГО сработавшего клиента — включая тех, кто пришёл бы и без стимула.
    # Поэтому база — activated, а не incremental. Это главная ловушка промо-механик.
    acq = g(k, "acq_cost_per_client"); gross = funnel["_activated_raw"]
    acq_total = gross * acq
    value_mode = is_value_mode(cfg); value = g(u, "value_per_client")

    # Форма притока: план продаж с сезонностью и темпом год-к-году, если он задан,
    # иначе равномерно по месяцам кампании. Уровень всегда берётся из инкремента,
    # чтобы каннибализация и deadweight не обходились планом стороной.
    # Норма LTV/CAC, от которой считается предельная цена привлечения и контакта.
    # Это не «хотелка», а порог, ниже которого масштабирование увеличивает убыток.
    hurdle = g(cfg.get("rice") or {}, "hurdle_ltv_cac", 0.0) or g(
        cfg.get("meta") or {}, "hurdle_ltv_cac", 0.0) or 3.0
    shape = acquisition_shape(cfg, h, camp_m)
    cohorts = [inc * w for w in shape]

    # Активная база считается до потоков: и спутник, и связанный клиент живут на ней.
    active_curve, a = [], 0.0
    for m in range(h):
        a = a * (1 - churn) + cohorts[m]
        active_curve.append(a)
    comp = companion_stream(cfg, active_curve, u)
    linked = linked_stream(cfg, active_curve)
    # Растранзачивание идёт по форме кампании, а не по кривой активной базы:
    # просыпается уже существующий клиент, когда до него дотянулась коммуникация.
    basact = base_activation_stream(cfg, shape, u)
    # Натуральный эффект — разовый выигрыш на когорту, а не живущая база: резерв
    # высвобождается один раз, обращение не поступает один раз. Поэтому затухания
    # тут нет, а откат назад задаётся relapse_share.
    natural = natural_effect_stream(cfg, shape, h)

    rows, cum, cum_disc, payback = [], 0.0, 0.0, None
    for m in range(h):
        cohort = cohorts[m]
        active = active_curve[m]
        if value_mode:
            # Ценность уже дисконтирована за свой горизонт ОТ МОМЕНТА привлечения и уже
            # включает отток, ramp и обслуживание. Строить помесячную маржу поверх неё —
            # значит посчитать жизнь клиента второй раз. Поэтому весь NPV когорты
            # признаётся в месяц привлечения, а дальше дисконтируется к нулевому месяцу.
            gm = cohort * value
        else:
            gm = active * monthly_margin(u, m)
        # Спутник и связанный клиент — доход СВЕРХ основного продукта, поэтому
        # добавляются и в режиме NPV продукта тоже: присланное число описывает
        # носителя продукта и про родителя с копилкой ничего не знает.
        gm_own = gm
        gm_comp = (comp[m]["margin"] + comp[m]["spend_fee"]) if comp else 0.0
        gm_linked = linked[m]["total"] if linked else 0.0
        gm_base = basact["rows"][m]["margin"] if basact else 0.0
        gm_nat = natural["rows"][m]["money"] if natural else 0.0
        gm += gm_comp + gm_linked + gm_base + gm_nat
        cost = ((media + acq_total) / camp_m if m < camp_m else 0.0)
        if m == 0:
            cost += g(k, "production") + g(k, "it_dev") + g(k, "one_off_other")
        cost += g(k, "opex_monthly")
        net = gm - cost; cum += net; cum_disc += net / ((1 + r_m) ** (m + 1))
        # В режиме NPV продукта накопленный поток смешивает дисконтированный NPV
        # с недисконтированными затратами, а момент возврата денег из одного числа
        # не выводится. Срок окупаемости честнее не считать вовсе — см. validate().
        #
        # Условие `gm_cum > 0` спасает от ложной окупаемости: при пустом медиаплане
        # и нулевых затратах накопленный поток равен нулю уже в первом месяце, и модель
        # рапортовала «окупаемость 1 месяц» там, где не происходило вообще ничего.
        gm_cum = sum(x["gross_margin"] for x in rows) + gm
        if not value_mode and payback is None and cum >= 0 and gm_cum > 0:
            payback = m + 1
        rows.append({"month": m + 1, "active_clients": active, "gross_margin": gm,
                     "margin_own": gm_own, "margin_companion": gm_comp,
                     "margin_linked": gm_linked, "margin_base_activation": gm_base,
                     "margin_natural": gm_nat,
                     "costs": cost, "net": net, "cumulative": cum, "cumulative_discounted": cum_disc})
    total_gm = sum(r["gross_margin"] for r in rows); total_cost = sum(r["costs"] for r in rows)
    # Маржа привлечённого клиента: свой продукт плюс то, что появляется вместе с ним.
    acq_gm = sum(r["margin_own"] + r["margin_companion"] + r["margin_linked"] for r in rows)
    mkt_cost = media + g(k, "production") + acq_total
    effect = effect_summary(u, rows, r_m, total_gm - total_cost, cum_disc)

    # Из чего сложилась маржа. Без этой разбивки нельзя увидеть главное свойство
    # таких инициатив: продукт может почти ничего не зарабатывать сам и при этом
    # окупаться связанным клиентом — а значит и управлять надо связанным клиентом.
    src = {"own_product": round(sum(r["margin_own"] for r in rows)),
           "companion": round(sum(r["margin_companion"] for r in rows)),
           "linked_client": round(sum(r["margin_linked"] for r in rows)),
           "base_activation": round(sum(r["margin_base_activation"] for r in rows)),
           "natural_effect": round(sum(r["margin_natural"] for r in rows))}
    # Доли считаются только когда все слагаемые одного знака. Если один источник
    # в минусе, «доля в марже» перестаёт что-либо значить: получаются 3454% у одного
    # и −2134% у другого. Вместо бессмысленного процента отдаём пустое и причину.
    parts = [src["own_product"], src["companion"], src["linked_client"],
             src["base_activation"], src["natural_effect"]]
    src["shares"] = ({kk: round(vv / total_gm, 4) for kk, vv in src.items()}
                     if total_gm > 0 and all(v >= 0 for v in parts) else None)
    if src["shares"] is None:
        src["shares_note"] = ("Доли не считаются: источники маржи разного знака "
                              "или суммарная маржа не положительна. Смотрите абсолютные суммы.")
    linked_detail = None
    if linked:
        linked_detail = {
            "linked_clients_peak": round(max(x["linked_clients"] for x in linked)),
            "retained_cum_end": round(linked[-1]["retained_cum"]),
            "income_acquisition": round(sum(x["income_acquisition"] for x in linked)),
            "income_retention": round(sum(x["income_retention"] for x in linked)),
            "income_cross_sell": round(sum(x["income_cross_sell"] for x in linked))}
    companion_detail = None
    if comp:
        companion_detail = {
            "name": (cfg.get("companion") or {}).get("name", "Спутник"),
            "holders_peak": round(max(x["holders"] for x in comp)),
            "balance_peak": round(max(x["balance_total"] for x in comp)),
            "margin_total": round(sum(x["margin"] for x in comp)),
            "spend_fee_total": round(sum(x["spend_fee"] for x in comp))}
    base_detail = None
    if basact:
        rws = basact["rows"]
        b_cfg = cfg.get("base_activation") or {}
        act_total = basact["activated_total"]
        margin_total = sum(x["margin"] for x in rws)
        # Экономика КОНТАКТА, а не клиента. На инициативах по своей базе решает не
        # конверсия, а арифметика канала: при сквозной конверсии 0,1% нужно почти
        # тысяча контактов на один результат, и продукт за сотни рублей их не окупит.
        conv = basact["conversion"]
        value_per_act = margin_total / act_total if act_total > 0 else 0.0
        base_detail = {
            "name": b_cfg.get("name", "Растранзачивание базы"),
            "activated_clients": round(act_total),
            "activated_from_stock": round(basact["activated_stock"]),
            "activated_from_flow": round(basact["activated_flow"]),
            "stock_exhausted_month": basact["stock_exhausted_month"],
            "margin_from_stock": round(sum(x["from_stock"] for x in rws)),
            "margin_from_flow": round(sum(x["from_flow"] for x in rws)),
            "peak_active_uplifted": round(max(x["active_uplifted"] for x in rws)),
            "end_active_uplifted": round(rws[-1]["active_uplifted"]),
            "turnover_delta_per_client": round(basact["turnover_delta"]),
            "balance_delta_per_client": round(basact["balance_delta"]),
            "margin_per_client_m": round(basact["margin_per_client_m"], 2),
            "turnover_uplift_total": round(sum(x["turnover_uplift"] for x in rws)),
            "margin_total": round(margin_total),
            "conversion": round(conv, 6),
            "contacts_per_activation": round(1 / conv) if conv > 0 else None,
            "value_per_activation": round(value_per_act),
            "max_cost_per_contact": round(value_per_act * conv / hurdle, 3)}
    natural_detail = None
    if natural:
        natural_detail = {
            "name": (cfg.get("natural_effect") or {}).get("name", "Натуральный эффект"),
            "unit": natural["unit"],
            "natural_total": round(natural["natural_total"]),
            "natural_from_stock": round(natural["natural_from_stock"]),
            "natural_from_flow": round(natural["natural_from_flow"]),
            "stock_exhausted_month": natural["stock_exhausted_month"],
            "monetization_rate": round(natural["monetization_rate"], 4),
            "adjustments_rate": round(natural["adjustments_rate"], 4),
            "net_rate": round(natural["net_rate"], 4),
            "money_total": round(natural["money_total"])}
    return {"horizon_months": h, "effect": effect,
            "natural_effect": natural_detail,
            "market": market_position(cfg, active_curve),
            "margin_sources": src, "linked": linked_detail, "companion": companion_detail,
            "base_activation": base_detail,
            "partner": partner_pl(cfg, active_curve, cohorts, u),
            "mode": MODE_VALUE if value_mode else MODE_BUILD_UP,
            "value_per_client": round(value) if value_mode else None,
            "gross_clients_paid": round(gross),
            "acquisition_payout_total": round(acq_total),
            "payout_per_incremental_client": round(acq_total / inc) if inc > 0 else None,
            "total_gross_margin": round(total_gm), "total_costs": round(total_cost),
            "net_effect": round(total_gm - total_cost), "npv": round(cum_disc), "payback_months": payback,
            "marketing_cost": round(mkt_cost),
            # ROMI по определению считается к МАРКЕТИНГОВЫМ затратам, и менять это нельзя —
            # иначе цифра перестанет быть сопоставимой с теми, что ходят по отчётам.
            # Но на инициативе, где маркетинг это малая часть расходов, она вводит
            # в заблуждение: IT-разработка на 500 млн роняет NPV в двенадцать раз,
            # а ROMI не двигает вовсе. Поэтому рядом считается отдача ко ВСЕМ затратам,
            # и доля маркетинга в них называется явно.
            "romi": round((total_gm - mkt_cost) / mkt_cost, 2) if mkt_cost > 0 else None,
            "roi_total": round((total_gm - total_cost) / total_cost, 2) if total_cost > 0 else None,
            "marketing_share_of_costs": (round(mkt_cost / total_cost, 3)
                                         if total_cost > 0 else None),
            "incremental_cpa": round(mkt_cost / inc) if inc > 0 else None,
            # LTV — маржа ПРИВЛЕЧЁННОГО клиента, поэтому в числителе только та маржа,
            # которую он приносит. Спутник и связанный клиент входят: копилка поверх карты
            # и родитель, платящий за ребёнка, появляются вместе с этим клиентом и без него
            # не появились бы. Растранзачивание и натуральный эффект НЕ входят: это деньги
            # действующей базы, которых движок намеренно не кладёт в инкремент, чтобы один
            # человек не посчитался дважды. Раньше в числителе стоял весь `total_gm`, и на
            # живом кейсе это завышало LTV, предельный CPA и допустимый бюджет на 30% —
            # тем сильнее, чем важнее для инициативы её собственная база.
            "acquired_gross_margin": round(acq_gm),
            "ltv_per_client": round(acq_gm / inc) if inc > 0 else None,
            "ltv_to_cac": round((acq_gm / inc) / (mkt_cost / inc), 2) if inc > 0 and mkt_cost > 0 else None,
            # Сколько вообще МОЖНО платить за клиента, а не сколько платим. Переворачивает
            # разговор: вместо «дайте бюджет, посчитаем что вышло» — «клиент стоит столько,
            # платить можно до стольки, ваш канал в это не влезает».
            "hurdle_ltv_cac": hurdle,
            "max_cpa": round((acq_gm / inc) / hurdle) if inc > 0 else None,
            "budget_at_max_cpa": round(inc * (acq_gm / inc) / hurdle) if inc > 0 else None,
            "breakeven_clients": round(total_cost / (acq_gm / inc)) if inc > 0 and acq_gm > 0 else None,
            "monthly": [{kk: (round(vv) if isinstance(vv, float) else vv) for kk, vv in r.items()} for r in rows]}

def evaluate(cfg):
    f = build_funnel(cfg)
    return f, run_pl(cfg, f)

SCEN = {"pessimistic": {"conv": 0.6, "margin": 0.75, "cannib_add": 0.15, "churn": 1.5},
        "base": {"conv": 1.0, "margin": 1.00, "cannib_add": 0.00, "churn": 1.0},
        "optimistic": {"conv": 1.3, "margin": 1.15, "cannib_add": -0.05, "churn": 0.8}}

def apply_scenario(cfg, s):
    c = copy.deepcopy(cfg)
    # множитель применяется РОВНО ОДИН РАЗ на канал: к последней ступени воронки
    # (cr_to_app) или к brand_uplift. Умножать и CTR, и CR — значит применить его дважды.
    for ch in c.get("channels", []):
        key = "brand_uplift" if ch.get("kind") == "brand" else "cr_to_app"
        if ch.get(key):
            ch[key] *= s["conv"]
    if is_value_mode(c):
        # NPV продукта на клиента уже содержит отток внутри себя, поэтому множитель оттока НЕ
        # применяем: иначе пессимизм считался бы дважды — и через NPV продукта, и через жизнь.
        # Пессимистичный сценарий здесь мягче, чем в build_up; если нужен жёстче —
        # присылайте пессимистичный NPV продукта отдельным числом.
        c["unit_economics"]["value_per_client"] = g(c["unit_economics"], "value_per_client") * s["margin"]
    else:
        for key in ("nim_annual", "interchange_rate", "fee_income_m"):
            if key in c["unit_economics"] and c["unit_economics"][key]:
                c["unit_economics"][key] *= s["margin"]
        c["unit_economics"]["monthly_churn"] = min(0.9, g(c["unit_economics"], "monthly_churn") * s["churn"])
    c["funnel"]["cannibalization"] = min(0.95, max(0.0, g(c["funnel"], "cannibalization") + s["cannib_add"]))
    return c

TORNADO = [("Конверсия в заявку (все каналы)", "conv"), ("Approval rate", ("funnel", "approval_rate")),
           ("Activation rate", ("funnel", "activation_rate")), ("Каннибализация", ("funnel", "cannibalization")),
           ("Средний баланс", ("unit_economics", "avg_balance")), ("Месячный оборот", ("unit_economics", "monthly_turnover")),
           ("Отток (churn)", ("unit_economics", "monthly_churn")), ("Кэшбэк", ("unit_economics", "cashback_rate")),
           ("Стоимость риска (CoR)", ("unit_economics", "cor_annual")),
    ("Deadweight (взяли бы и так)", ("funnel", "deadweight")),
    ("Выплата за привлечение", ("costs", "acq_cost_per_client")),
    ("Доля растранзаченных (uplift над контролем)", ("base_activation", "uplift_rate")),
    ("Оборот после растранзачивания", ("base_activation", "turnover_after"))]

# В режиме NPV продукта драйверов юнит-экономики нет — вместо шести отдельных допущений
# одно число из чужой модели. Крутить его компоненты у нас нечем и незачем.
TORNADO_VALUE = [("Конверсия в заявку (все каналы)", "conv"),
                 ("Approval rate", ("funnel", "approval_rate")),
                 ("Activation rate", ("funnel", "activation_rate")),
                 ("Каннибализация", ("funnel", "cannibalization")),
                 ("NPV продукта на клиента", ("unit_economics", "value_per_client")),
                 ("Deadweight (взяли бы и так)", ("funnel", "deadweight")),
                 ("Выплата за привлечение", ("costs", "acq_cost_per_client")),
                 ("Доля растранзаченных (uplift над контролем)",
                  ("base_activation", "uplift_rate")),
                 ("Оборот после растранзачивания", ("base_activation", "turnover_after"))]


# Драйверы, у которых движение на ±25% бессмысленно. Срок жизни эффекта — главный
# из них: вопрос не «на четверть дольше», а «держится только окно замера или не гаснет
# вовсе». На живых цифрах между этими границами разница в четыре с лишним раза, и без
# этой строки человек видит чувствительность к кэшбэку, но не к самому большому
# допущению расчёта.
def _decay_bounds(cfg):
    """Нижняя граница — сколько наблюдали (дальше окна замера доказательств нет).
    Верхняя — ноль, то есть «не затухает» в терминах движка."""
    obs = g(cfg.get("pilot") or {}, "duration_months")
    cur = g(cfg.get("base_activation") or {}, "decay_months")
    low = obs or cur or 6.0
    return [low, 0.0]


TORNADO_EXPLICIT = [("Срок жизни эффекта: от окна замера до «не гаснет»",
                     ("base_activation", "decay_months"), _decay_bounds)]


def tornado(cfg, base_npv, delta=0.25):
    out = []
    for label, path, bounds in TORNADO_EXPLICIT:
        sec, key = path
        # раздела нет — драйвера нет; ноль в самом ключе при этом законен
        # и означает «не затухает», поэтому проверяем РАЗДЕЛ, а не значение
        if not isinstance(cfg.get(sec), dict):
            continue
        swing = []
        for v in bounds(cfg):
            c = copy.deepcopy(cfg)
            c[sec][key] = v
            swing.append(evaluate(c)[1]["npv"])
        out.append({"driver": label, "npv_low": min(swing), "npv_high": max(swing),
                    "swing": abs(swing[1] - swing[0]),
                    "swing_pct_of_base": round(abs(swing[1] - swing[0]) / abs(base_npv) * 100, 1)
                    if base_npv else None})
    for label, path in (TORNADO_VALUE if is_value_mode(cfg) else TORNADO):
        swing = []
        for mult in (1 - delta, 1 + delta):
            c = copy.deepcopy(cfg)
            if path == "conv":
                for ch in c.get("channels", []):
                    key = "brand_uplift" if ch.get("kind") == "brand" else "cr_to_app"
                    if ch.get(key):
                        ch[key] *= mult
            else:
                sec, key = path
                if not c.get(sec, {}).get(key):
                    swing = None; break
                c[sec][key] *= mult
            swing.append(evaluate(c)[1]["npv"])
        if swing:
            out.append({"driver": label, "npv_low": min(swing), "npv_high": max(swing),
                        "swing": abs(swing[1] - swing[0]),
                        "swing_pct_of_base": round(abs(swing[1] - swing[0]) / abs(base_npv) * 100, 1) if base_npv else None})
    return sorted(out, key=lambda x: -x["swing"])

def rice_score(cfg, incremental_clients, npv, pl=None):
    """RICE. Охват — «скольких людей это затрагивает», а не «скольких мы привлекли».

    Раньше в отсутствие явного значения брался инкремент, и у инициативы БЕЗ привлечения
    охват выходил нулём: движок намеренно не кладёт растранзаченных в инкремент, чтобы
    один человек не посчитался дважды. Итог — RICE = 0 при NPV в десятки миллионов, молча,
    у каждой фичи и каждой доработки. Именно у того класса, ради приоритизации которого
    RICE и считают.

    Порядок источников: явно заданное → инкремент привлечения → те, чьё поведение
    изменила инициатива на своей базе. Если охвата нет ни по одному — RICE не считается
    вовсе. Ноль здесь не ответ, а признание, что мерить нечем: у инициативы, которая
    никого не затрагивает, нет и приоритета.
    """
    r = cfg.get("rice")
    if not r:
        return None
    reach, source = g(r, "reach"), "задан вручную"
    if not reach:
        reach, source = incremental_clients, "инкремент привлечения"
    if not reach:
        # своя база: тот, чьё поведение изменилось, — это и есть охват инициативы
        det = (pl or {}).get("base_activation") or {}
        reach = det.get("activated_clients") or 0
        source = "клиенты, сменившие поведение (своя база)"
    if not reach:
        return {"reach": 0, "impact": g(r, "impact"), "confidence": g(r, "confidence"),
                "effort_person_months": g(r, "effort"), "rice_score": None,
                "reach_source": "нечем — охват не задан и не выводится",
                "npv_per_person_month": round(npv / max(g(r, "effort", 1), 0.1))}
    score = reach * g(r, "impact", 1) * g(r, "confidence", 0.5) / max(g(r, "effort", 1), 0.1)
    return {"reach": round(reach), "impact": g(r, "impact"), "confidence": g(r, "confidence"),
            "effort_person_months": g(r, "effort"), "rice_score": round(score, 1),
            "reach_source": source,
            "npv_per_person_month": round(npv / max(g(r, "effort", 1), 0.1))}

def frk_ladder(cfg):
    """ФРК 4 ⊂ 3 ⊂ 2 ⊂ 1. Считаем каждый уровень и предельную отдачу шага вверх."""
    tiers = sorted({int(c["frk_tier"]) for c in cfg.get("channels", []) if c.get("frk_tier")}, reverse=True)
    if len(tiers) < 2:
        return None
    out, prev = [], None
    for t in tiers:
        c = copy.deepcopy(cfg)
        c["channels"] = [ch for ch in cfg["channels"] if int(ch.get("frk_tier", 9)) >= t]
        f, p = evaluate(c)
        row = {"frk_tier": t, "budget": round(f["_media_cost"]), "reach_share": f["combined_reach_share"],
               "avg_frequency": f["avg_frequency"], "incremental_clients": f["incremental_clients"],
               "npv": p["npv"], "romi": p["romi"], "incremental_cpa": p["incremental_cpa"],
               "payback_months": p["payback_months"]}
        if prev:
            d_b = row["budget"] - prev["budget"]; d_c = row["incremental_clients"] - prev["incremental_clients"]
            row.update({"step_from_frk": prev["frk_tier"], "marginal_budget": d_b, "marginal_clients": d_c,
                        "marginal_cpa": round(d_b / d_c) if d_c > 0 else None,
                        "marginal_npv": row["npv"] - prev["npv"], "step_worth_it": row["npv"] - prev["npv"] > 0})
        out.append(row); prev = row
    best = max(out, key=lambda x: x["npv"])
    return {"levels": out, "optimal_tier": best["frk_tier"],
            "note": f"Максимальный NPV даёт ФРК {best['frk_tier']}. Шаги с отрицательным marginal_npv не окупаются."}

def validate_brief(cfg):
    """Что человек сказал, а модель не посчитала.

    Самый дорогой класс ошибок в этом инструменте — не завышенный NPV, а тихая потеря
    вводных. Человек рассказал про пилот, партнёров и географию, модель молча посчитала
    без них и выдала уверенный ответ. Неверную цифру видно, а эту нет. Поэтому всё,
    что не стало параметром, печатается — списком, дословно, с причиной.
    """
    b = brief_coverage(cfg)
    if not b:
        return ["БРИФ НЕ РАЗОБРАН: описание инициативы не разложено на утверждения. "
                "Всё, что человек сказал словами — про пилот, партнёров, географию, "
                "механику, — в расчёт не попало. Заполните раздел brief: каждое "
                "утверждение получает статус applied / question / ignored."]
    out = []
    if b["questions"]:
        lines = "; ".join(f"«{q['claim']}» → {q['implication'] or 'нужен ответ'}"
                          for q in b["open_questions"])
        out.append(f"БРИФ, ОТКРЫТЫЕ ВОПРОСЫ ({b['questions']}): {lines}. "
                   "Пока на них нет ответа, соответствующие параметры взяты дефолтом — "
                   "то есть догадкой, а не тем, что человек имел в виду.")
    if b["ignored"]:
        lines = "; ".join(f"«{q['claim']}» — {q['reason']}" for q in b["not_counted"])
        out.append(f"БРИФ, НЕ УЧТЕНО В РАСЧЁТЕ ({b['ignored']}): {lines}. "
                   "Это сознательное решение, а не забывчивость, но в отчёт оно "
                   "обязано попасть: читатель должен знать, чего в цифре нет.")
    if b["coverage"] is not None and b["coverage"] < 0.5:
        out.append(f"БРИФ РАЗОБРАН НАПОЛОВИНУ: параметрами стали {b['applied']} "
                   f"утверждений из {b['total']} ({b['coverage']:.0%}). Модель считает "
                   "не ту инициативу, которую описал человек, а её обеднённую версию.")
    return out


def validate_extensions(cfg, f, pl):
    """Проверки новых блоков: план, рынок, связанный клиент, спутник, партнёр."""
    out = list(validate_brief(cfg))
    # План продаж против медиаплана. Обычно между ними пропасть, и она либо
    # означает переоценённый план, либо недофинансированный маркетинг.
    if f.get("activated_from_plan") is not None:
        plan, media = f["activated_from_plan"], f["activated_from_media"]
        out.append(f"ОБЪЁМ ИЗ ПЛАНА: считаем по плану продаж ({nf(plan)} клиентов), "
                   f"а не по медиаворонке.")
        if plan > media * 1.2:
            gap = plan - media
            out.append(
                f"РАЗРЫВ ПЛАНА И МЕДИА: медиаплан даёт {nf(media)} активаций, план обещает "
                f"{nf(plan)}. Откуда берутся ещё {nf(gap)} ({gap / max(plan, 1):.0%})? Если из "
                "органики и полки — так и записать отдельной строкой; если ниоткуда — "
                "план не обеспечен, и это надо решить до запуска, а не в декабре."
                )
        elif media > plan * 1.5 and media > 0:
            out.append(f"ВНИМАНИЕ: медиаплан даёт {nf(media)} активаций — заметно больше плана "
                       f"продаж ({nf(plan)}). Либо план занижен, либо бюджет избыточен: "
                       "в расчёт идёт план, лишний медиабюджет просто списывается в затраты."
                       )
    mk = pl.get("market")
    if mk and mk["explicit"]:
        sh = mk["share_of_reachable"]
        if sh >= 0.4:
            out.append(f"ДОЛЯ РЫНКА: план выводит на {sh:.0%} достижимого рынка "
                       f"({nf(mk['clients_peak'])} из {nf(mk['reachable_market'])}). Это уже не план "
                       "привлечения, а заявка на лидерство в сегменте — она требует отдельного "
                       "обоснования: чем именно вы заберёте эту долю у тех, кто её сейчас держит."
                       )
        elif sh >= 0.2:
            out.append(f"ДОЛЯ РЫНКА: план выводит на {sh:.0%} достижимого рынка — высоко, "
                       "но достижимо. Сверьтесь с фактической динамикой доли за прошлые запуски."
                       )
        if mk.get("share_of_total") and mk["share_of_total"] != sh:
            out.append(f"ДОЛЯ СЧИТАЕТСЯ ОТ ДОСТИЖИМОГО РЫНКА: {sh:.1%} от "
                       f"{nf(mk['reachable_market'])}, а не {mk['share_of_total']:.1%} от всего "
                       f"рынка {nf(mk['market_total'])}. Вторая цифра красивее и не значит ничего: "
                       "недостижимая часть рынка не купит продукт ни при каком бюджете."
                       )

    lc = cfg.get("linked_client")
    if lc:
        if g(lc, "margin_rate", 1.0) >= 1.0:
            out.append("ОД ≠ МАРЖА: у связанного клиента margin_rate не задан, значит его "
                       "операционный доход целиком засчитан как маржа. ОД не вычитает "
                       "стоимость фондирования, обслуживание и риск — эффект завышен. "
                       "Задайте margin_rate долей маржи в ОД.")
        if g(lc, "selection_haircut", 1.0) >= 1.0 and g(lc, "churn_reduction"):
            out.append(
                "ПОПРАВКА НА ОТБОР НЕ ЗАДАНА: удержанному связанному клиенту засчитан "
                "средний ОД. Но он отобран по признаку — собирался уйти, значит вовлечён "
                "хуже среднего по определению. Средний NPV продукта, приложенный к худшей "
                "части базы, завышает эффект удержания. Задайте selection_haircut.")
        if g(lc, "attribution_share", 1.0) >= 1.0 and (lc.get("sources") or {}).get("new"):
            out.append("АТРИБУЦИЯ 100%: весь ОД новых связанных клиентов приписан инициативе. "
                       "Часть из них пришла бы и без неё — задайте attribution_share.")
        if not lc.get("sources"):
            out.append("ИСТОЧНИКИ НЕ ЗАДАНЫ: связанные клиенты целиком считаются новыми. "
                       "Если часть из них уже клиенты банка, их ОД инициатива себе приписала.")
        sh = (pl["margin_sources"].get("shares") or {}).get("linked_client")
        if sh and sh > 0.6:
            out.append(f"ЭФФЕКТ ДЕРЖИТСЯ НА СВЯЗАННОМ КЛИЕНТЕ: {sh:.0%} маржи приходит "
                       "не с носителя продукта. Это не ошибка — так устроены детские и "
                       "семейные продукты, — но управлять надо именно этим эффектом, "
                       "и замерять его нужно контрольной группой по СВЯЗАННЫМ клиентам.")
    # Продукт не окупает сам себя и живёт за счёт пристроек. Вывод не «плохо»,
    # а «вы запускаете не то, чем думаете»: цели, KPI и владелец продукта должны
    # быть про связанного клиента, иначе команда будет оптимизировать не ту метрику.
    ms = pl.get("margin_sources") or {}
    if ms.get("own_product", 0) < 0 and (ms.get("linked_client", 0) or ms.get("companion", 0)):
        rescue = ms.get("companion", 0) + ms.get("linked_client", 0)
        out.append(
            f"ПРОДУКТ САМ ПО СЕБЕ УБЫТОЧЕН: носитель даёт {nf(ms['own_product'])} за горизонт, "
            f"а спутник и связанный клиент — {nf(rescue)}. Инициатива держится не на том "
            "продукте, который выводится. Проверьте: если эффект на связанного клиента "
            "не подтвердится замером, остаётся чистый убыток."
            )
    ne, ned = cfg.get("natural_effect"), pl.get("natural_effect")
    if ne and ned:
        out.append(
            f"НАТУРАЛЬНЫЙ ЭФФЕКТ: {nf(ned['natural_total'])} {ned['unit']} — это НЕ деньги "
            f"банка. В P&L приходит {nf(ned['money_total'])} ₽: перевод по ставке "
            f"{ned['monetization_rate']:.0%}"
            + (f" минус вычеты {abs(ned['adjustments_rate']):.0%}"
               if ned["adjustments_rate"] else "")
            + f", итого {ned['net_rate']:.0%} от натуральной величины. "
              "В отчёт выносить обе цифры и в разных строках.")
        if g(ne, "monetization", 1.0) >= 1.0 and not (ne.get("parts") or []):
            out.append(
                "СТАВКА ПЕРЕВОДА НЕ ЗАДАНА: monetization = 1, то есть натуральный эффект "
                "засчитан деньгами один к одному. Так почти никогда не бывает. Снижение "
                "просрочки даёт высвобожденный резерв, а не сумму долга; сокращение "
                "обращений — сэкономленные минуты, а не выручку. Задайте ставку явно — "
                "именно на этом шаге живые ФЭО завышают эффект в разы.")
        if not (ne.get("adjustments") or []):
            out.append(
                "ВЫЧЕТОВ НЕТ: инструмент, которым достигается эффект, почти всегда чего-то "
                "стоит. Реструктуризация — недополученный процент, скидка — маржа, отсрочка "
                "достаётся и тем, кто заплатил бы сам. Пустой список вычетов — это "
                "утверждение «инструмент бесплатный», и его надо обосновать.")
        if not ne.get("measured_against_control"):
            out.append(
                "НАТУРАЛЬНЫЙ ЭФФЕКТ БЕЗ КОНТРОЛЯ: uplift_rate задан без замера против "
                "контрольной группы, значит в него попал естественный уровень. У просрочки "
                "это базовый возврат, у обращений — сезонность. Все цифры блока — грейд C.")
        if not (ne.get("parts") or []):
            out.append(
                "БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ: эффект применён ко всей базе одной ставкой. "
                "Обычно он возможен не везде — 90+ дней просрочки диалогом не лечится, "
                "и вся сумма там, где эффекта нет. Разложите базу на parts с потолками.")
        if ned["natural_from_flow"] <= 0 and ned["natural_from_stock"] > 0:
            out.append(
                f"ТОЛЬКО СТОК: натуральная база конечна и вырабатывается за "
                f"{ned['stock_exhausted_month'] or '—'} мес. Дальше эффект прекращается, "
                "а постоянные затраты остаются. Задайте monthly_inflow, если база "
                "пополняется.")

    # Срок жизни эффекта обычно не знает никто: замер всегда короче горизонта.
    # Спорить о его величине бессмысленно — полезнее знать, при каком сроке
    # меняется решение.
    if isinstance(cfg.get("base_activation"), dict):
        _be = breakeven_decay(cfg)
        _obs = g(cfg.get("pilot") or {}, "duration_months")
        if _be is None:
            out.append(
                "СРОК ЖИЗНИ ЭФФЕКТА НА РЕШЕНИЕ НЕ ВЛИЯЕТ: знак NPV одинаков и при "
                "затухании за месяц, и при полном его отсутствии. Уточнять срок "
                "незачем — решение держится на другом.")
        else:
            out.append(
                f"РЕШЕНИЕ ДЕРЖИТСЯ НА СРОКЕ ЖИЗНИ ЭФФЕКТА: NPV выходит в ноль при "
                f"затухании за {_be:g} мес. Дольше — окупается, короче — нет."
                + (f" Замеряли {_obs:g} мес, то есть порог "
                   + ("внутри окна замера: он подтверждён наблюдением."
                      if _be <= _obs else
                      "ЗА пределами наблюдения — держится ли эффект столько, "
                      "мы не видели и из этих данных не узнаем.")
                   if _obs > 0 else ""))

    if isinstance(pl.get("base_activation"), dict) or isinstance(pl.get("natural_effect"), dict):
        out.append(
            "СЦЕНАРИИ НЕ ТРОГАЮТ ДРАЙВЕРЫ СВОЕЙ БАЗЫ: пессимистичный и оптимистичный "
            "двигают конверсию, ставки, отток и каннибализацию. Uplift растранзачивания, "
            "монетизация натурального эффекта и откат остаются как заданы. Чем больше "
            "эффекта приходит с действующей базы, тем уже становится разброс сценариев — "
            "вплоть до трёх одинаковых чисел. Стресс-тест по этим допущениям делайте "
            "руками: поменяйте uplift и пересчитайте.")

    ba, bad = cfg.get("base_activation"), pl.get("base_activation")
    # Проверка ДО `if ba and bad`: именно здесь bad равен нулю, и старое условие
    # молчало ровно в том случае, ради которого предупреждение и нужно. Человек
    # заполнил весь блок, движок вернул ноль и не сказал почему.
    if isinstance(ba, dict) and is_value_mode(cfg) and bad:
        # Клиенты просыпаются, оборот растёт, денег ноль — маржа на клиента в месяц
        # равна нулю, потому что обнулены ставки. Смотрим именно на неё: сам блок
        # при этом посчитан и в выдаче присутствует, поэтому его наличие ни о чём
        # не говорит.
        _grew = (g(ba, "turnover_after") > g(ba, "turnover_before")
                 or g(ba, "balance_after") > g(ba, "balance_before"))
        if _grew and bad.get("activated_clients", 0) > 0 \
                and not bad.get("margin_per_client_m"):
            out.append(
                "РАСТРАНЗАЧИВАНИЕ НЕ ПОСЧИТАНО: у продукта задана готовый NPV продукта, "
                "поэтому ставки маржи снизу вверх обнулены — переводить прирост оборота "
                "и остатка не во что, и блок дал ровно ноль. NPV продукта на клиента описывает "
                "НОВОГО клиента, а не изменение поведения существующего: это разные вещи, "
                "и вторую она не покрывает. Либо задайте ставки (режим build_up), либо "
                "считайте растранзачивание отдельной инициативой.")

    if ba and bad:
        if not ba.get("measured_against_control"):
            out.append(
                "РАСТРАНЗАЧИВАНИЕ БЕЗ КОНТРОЛЬНОЙ ГРУППЫ: uplift_rate задан без замера "
                "против контроля. Спящая база оживает и сама — сезонностью, зарплатой, "
                "прочими активностями. Без контроля вы приписываете инициативе чужой "
                "прирост, и проверить это уже нечем. Все цифры блока — грейд C, "
                "а вердикт по ним принимать нельзя.")
        if g(ba, "technically_reachable", 1.0) >= 1.0:
            out.append(
                "ДОСТИЖИМОСТЬ НЕ ЗАДАНА: technically_reachable = 1, то есть предполагается, "
                "что сообщение дойдёт до всей базы. У части нет приложения, часть в стоп-листе. "
                "Это не конверсия, а физическая возможность доставить — и лечится она "
                "совсем не тем, чем низкий отклик.")
        if not (ba.get("parts") or []):
            out.append(
                "СПЯЩАЯ БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ: один uplift_rate на всех. Спящий месяц "
                "и спящий год откликаются по-разному, и в глубокой части эффект близок "
                "к нулю. Разложите базу на parts с потолками — иначе весь эффект "
                "приписывается тем, у кого его нет.")
        if g(ba, "decay_months") <= 0:
            out.append(
                "ЭФФЕКТ БЕЗ ЗАТУХАНИЯ: decay_months = 0, то есть проснувшийся клиент "
                "остаётся активным до конца горизонта. Так почти не бывает: всплеск "
                "транзакционной активности после коммуникации гаснет за несколько месяцев. "
                "Задайте срок затухания явно — это допущение, а не его отсутствие.")
        if g(ba, "attribution_share", 1.0) >= 1.0 and ba.get("measured_against_control"):
            out.append(
                "АТРИБУЦИЯ 100% при замере против контроля — это законно: uplift над "
                "контролем уже инкрементален по построению. Оставлено как есть.")
        # Сток кончается, поток — нет. Если инициативу обосновали накопленной базой,
        # через workout_months эффект обнулится, а run-rate останется.
        if bad["activated_from_flow"] <= 0 and bad["activated_from_stock"] > 0:
            out.append(
                f"ТОЛЬКО СТОК: база в {nf(g(ba, 'eligible_clients'))} человек конечна и "
                f"прорабатывается за {bad['stock_exhausted_month'] or '—'} мес, после чего "
                "эффект гаснет, а постоянные затраты остаются. Задайте monthly_inflow: "
                "если в это состояние никто не попадает заново, это разовая акция, "
                "а не сервис — и горизонт у неё должен быть коротким.")
        elif bad["activated_from_stock"] > 0 and bad["margin_from_flow"] > bad["margin_from_stock"]:
            out.append(
                f"ПОТОК ВАЖНЕЕ СТОКА: накопленная база даёт {nf(bad['margin_from_stock'])} "
                f"один раз, ежемесячное пополнение — {nf(bad['margin_from_flow'])} за горизонт. "
                "Обосновывать инициативу надо потоком, хотя сток выглядит внушительнее.")
        # Экономика контакта: обычно именно здесь инициатива и умирает
        if bad.get("max_cost_per_contact") is not None:
            out.append(
                f"ЦЕНА КОНТАКТА: чтобы окупиться при норме LTV/CAC {pl.get('hurdle_ltv_cac')}, "
                f"один контакт должен стоить не дороже {bad['max_cost_per_contact']} ₽. "
                f"На один результат уходит {nf(bad['contacts_per_activation'])} контактов, "
                f"а сам результат стоит {nf(bad['value_per_activation'])} ₽. Сверьте с "
                "прайсом канала: платные смс такие инициативы обычно не выдерживают.")
        out.append(
            f"РАСТРАНЗАЧЕНО НЕ ЗНАЧИТ ПРИВЛЕЧЕНО: {nf(bad['activated_clients'])} клиентов "
            "сменили поведение, но они УЖЕ в базе. В АКБ и в инкремент они намеренно "
            "не попадают — иначе один человек посчитался бы дважды, как новый и как "
            "проснувшийся. Растёт ОД и оборот, а не число клиентов.")
        # Тот же медиабюджет не может одновременно приводить новых и будить спящих
        # в полную силу: это один и тот же контакт. Разводить их должен человек.
        if f.get("incremental_clients", 0) > 0:
            out.append(
                f"ПРОВЕРЬТЕ ПЕРЕСЕЧЕНИЕ: в одном прогоне и привлечение "
                f"({nf(f['incremental_clients'])} новых), и растранзачивание "
                f"({nf(bad['activated_clients'])} проснувшихся). Если это один и тот же "
                "медиаконтакт, часть бюджета засчитана дважды. Убедитесь, что охват базы "
                "и охват рынка заданы разными каналами.")

    comp = cfg.get("companion")
    if comp and comp.get("spend_returns_to_turnover"):
        out.append(f"ПРОВЕРЬТЕ ДВОЙНОЙ СЧЁТ: траты из «{comp.get('name', 'спутника')}» приносят "
                   "комиссию повторно. Это верно, только если спутник пополняется извне "
                   "(подарки, переводы родных). Если он пополняется с основной карты, "
                   "тот же оборот посчитан дважды — снимите флаг.")
    pt = pl.get("partner")
    if pt and pt["net"] < 0:
        out.append(f"ПАРТНЁР В МИНУСЕ: «{pt['name']}» получает {nf(pt['net'])} за горизонт. "
                   "Инициатива, убыточная для того, без кого она не работает, не запускается — "
                   "это выясняется на переговорах, а не после.")
    return out


def validate(cfg, f, pl):
    """Проверки ВВОДНЫХ: ловят случаи, где модель считает без ошибок, но молча теряет часть эффекта."""
    out = list(cfg.get("_segments", {}).get("notes", [])) + list(cfg.get("_notes", []))
    out += validate_extensions(cfg, f, pl)
    h = int(g(cfg["meta"], "horizon_months", 24)); cm = int(max(g(cfg["costs"], "campaign_months", 1), 1))
    if h < cm:
        out.append(f"ОШИБКА ВВОДА: горизонт {h} мес короче кампании {cm} мес. "
                   f"В расчёт попадёт только {h}/{cm} медиабюджета и притока клиентов — цифры занижены. "
                   "Поставьте горизонт не меньше длительности кампании.")
    # Пустой медиаплан: считать нечего, но модель раньше молча возвращала нули и вердикт
    # «NO-GO — базовый сценарий не окупается», как будто это оценка инициативы.
    # Инициатива без привлечения — законный случай, если эффект идёт на своей базе.
    # Иначе эти две проверки кричат «ошибка ввода» на корректном прогоне про
    # растранзачивание или удержание.
    # Продукт не задан — значит молча не применились возрастные правила банка, каталог
    # NPV продукта и проверка стоимости риска. Молчание здесь опаснее ошибки:
    # расчёт выглядит полным, а три механизма из него выпали.
    if not g(cfg.get("meta") or {}, "product", ""):
        out.append("ПРОДУКТ НЕ ЗАДАН: `meta.product` пуст, поэтому не применены возрастные "
                   "правила банка, каталог NPV продукта и проверка Cost of Risk. "
                   "Расчёт корректен арифметически, но эти три механизма в нём не работали.")

    base_only = has_effect_beyond_acquisition(pl)
    if not cfg.get("channels") and not is_plan_driven(cfg) and not base_only:
        out.append("ОШИБКА ВВОДА: в плане нет ни одного канала. Показов, охвата и заявок "
                   "не будет, инкремент равен нулю — считать нечего. Это не оценка "
                   "инициативы, а пустой прогон: добавьте каналы на шаге «Допущения» "
                   "или задайте объём планом продаж (раздел acquisition).")
    elif (cfg.get("channels") and not base_only
          and f["incremental_clients"] <= 0 and f["applications_total"] <= 0):
        out.append("ОШИБКА ВВОДА: каналы заданы, но заявок ноль. Проверьте бюджет, CPM "
                   "и конверсии: при нулевой конверсии или нулевом бюджете воронка пустая, "
                   "а результат выглядит как посчитанный.")
    if base_only and f["incremental_clients"] <= 0:
        out.append("ИНИЦИАТИВА БЕЗ ПРИВЛЕЧЕНИЯ: новых клиентов нет, весь эффект идёт "
                   "на существующей базе. Это законный тип, а не пустой прогон — "
                   "но метрики привлечения (CPA, LTV/CAC, точка безубыточности "
                   "в клиентах) для него не считаются и в отчёт не выносятся.")
    if g(cfg["audience"], "target_size") <= 0:
        out.append("ОШИБКА ВВОДА: размер ЦА = 0. Охват, частота и весь brand-эффект молча обнуляются, "
                   "но прямые заявки остаются — результат выглядит правдоподобно и при этом неверен.")
    if g(cfg["audience"], "baseline_organic_apps_m") <= 0 and any(
            c.get("kind") == "brand" for c in cfg.get("channels", [])):
        out.append("ОШИБКА ВВОДА: в плане есть охватные каналы (ТВ/наружка), но органический поток заявок = 0. "
                   "Brand-эффект считается как прирост к органике, поэтому весь охватный бюджет даёт ровно 0 заявок.")
    if is_value_mode(cfg):
        u = cfg["unit_economics"]
        vh = int(g(u, "value_horizon_months", h))
        out.append(f"РЕЖИМ: экономика задана готовым NPV продукта "
                   f"({nf(round(g(u, 'value_per_client')))} ₽ за {vh} мес, дисконтированной). "
                   "Срок окупаемости в этом режиме НЕ считается: момент возврата денег "
                   "из одного числа не выводится. Нужна окупаемость — задавайте юнит-экономику "
                   "снизу вверх (mode = build_up).")
        if g(u, "value_per_client") <= 0:
            out.append("ОШИБКА ВВОДА: режим NPV продукта на клиента включён, но само число = 0. "
                       "Вся валовая маржа обнулится, останутся одни затраты.")
        if vh != h:
            out.append(f"ОШИБКА ВВОДА: NPV продукта посчитан за {vh} мес, а горизонт оценки {h} мес. "
                       "Затраты считаются на свой горизонт, а NPV приходит целиком — "
                       "цифры несопоставимы. Выровняйте горизонты.")
        # Отток НЕ влияет на NPV в этом режиме — он уже внутри присланного числа. Но АКБ,
        # ОД и AUM считаются по живой кривой активной базы, и при нулевом оттоке клиент
        # не отваливается никогда: клиенто-месяцы, а с ними ОД и AUM, уходят в потолок.
        if g(u, "monthly_churn") <= 0 and (g(u, "operating_income_annual") or g(u, "avg_balance")):
            out.append("ВНИМАНИЕ: отток = 0. На NPV это не влияет (отток внутри NPV продукта на клиента), "
                       "но АКБ, ОД и AUM считаются по активной базе — при нулевом оттоке она "
                       "не убывает, и эти три цифры завышены. Задайте отток хотя бы 1–2% в месяц.")
        # Средний баланс из списка исключён намеренно: маржи он здесь не даёт, но остаётся
        # единственным источником AUM и дополнительного объёма средств.
        stale = [k for k in ("monthly_turnover", "nim_annual", "interchange_rate",
                             "cashback_rate", "servicing_cost_m", "cor_annual") if g(u, k)]
        if stale:
            out.append(f"ВНИМАНИЕ: в режиме NPV продукта на клиента не участвуют {', '.join(stale)} — "
                       "они заданы, но на результат не влияют. Отток и ramp-up тоже внутри "
                       "присланного числа, поэтому множитель оттока в сценариях не применяется.")
    elif g(cfg["unit_economics"], "monthly_churn") <= 0:
        out.append("ВНИМАНИЕ: отток = 0, клиент живёт вечно. LTV и NPV завышены — поставьте хотя бы 1–2% в месяц.")

    # Молчаливое отсутствие цифры — та же ошибка, что молчаливый дефолт: человек видит
    # пустую клетку и не знает, это ноль эффекта или отсутствие данных.
    if not g(cfg["unit_economics"], "operating_income_annual"):
        blind = [f"{r['name']}" for r in cfg.get("_segments", {}).get("rows", [])
                 if r.get("effective_audience", 0) > 0 and not r.get("operating_income_annual")]
        out.append("ДАННЫХ НЕТ: операционный доход на клиента не задан, поэтому эффект в ОД "
                   "не посчитан и в отчёте будет пустым."
                   + (f" Нет данных по возрастам сегментов: {', '.join(blind)}." if blind else "")
                   + " Остальные валюты — АКБ, AUM и PL — считаются как обычно.")

    # насыщение канала: добавочный бюджет уже не работает
    for c in f["channels"]:
        # Канал съедает бюджет и не даёт ничего. Обычно это неверный тип: internal считает
        # отклик от «охвата базы» и не смотрит на CPM и CTR, поэтому perform-канал, помеченный
        # как internal, тихо превращает весь бюджет в ноль заявок. Модель при этом считает
        # без ошибок — ровно тот случай, который ловит этот блок.
        if c["budget"] > 0 and c["applications"] == 0 and not c["brand_contribution"]:
            need = {"internal": "«Охват базы» и response rate",
                    "perform": "CPM и CTR", "brand": "CPM и brand uplift"}[c["kind"]]
            out.append(f"БЮДЖЕТ ВПУСТУЮ: канал «{c['name']}» типа {c['kind']} тратит "
                       f"{nf(c['budget'])} ₽ и не даёт ни одной заявки и ни капли brand-эффекта. "
                       f"Для типа {c['kind']} нужны {need} — проверьте тип канала и конверсии.")
        if c["kind"] == "brand" and c["reach_share"] >= 0.97 and c["freq"] >= 4.5:
            out.append(f"НАСЫЩЕНИЕ: канал «{c['name']}» упёрся в потолок — охват {c['reach_share']:.0%}, "
                       f"частота {c['freq']}. Дополнительный бюджет сюда НЕ даёт дополнительных заявок вообще. "
                       "Деньги эффективнее перебросить в другой канал или расширить ЦА.")
        if c["kind"] == "perform" and c["reach_share"] >= 0.97 and c["freq"] >= 8:
            out.append(f"ВНИМАНИЕ: «{c['name']}» — частота {c['freq']} при полном охвате. Модель считает отклик "
                       "линейно от показов, но на практике здесь уже работает выгорание: занизьте CTR на 15–30%.")
    return out

def flags(cfg, f, pl):
    """Красные флаги. Список обязан совпадать с блоком «КРАСНЫЕ ФЛАГИ» листа «Итог» —
    расхождение здесь означает, что движок и книга дают разные предупреждения на одних данных."""
    out = []
    if f["avg_frequency"] > 12:
        out.append(f"Средняя частота {f['avg_frequency']} контактов — сильный перебор (эффективная 3–6). Бюджет уходит в переконтакт.")
    if f["combined_reach_share"] > 0.95:
        out.append("Охват упёрся в потолок ЦА — рост бюджета даёт только частоту, не новых людей.")
    br = f["applications_brand_lift"]
    if br and br / max(f["applications_total"], 1) > 0.4:
        out.append(f"{round(br / f['applications_total'] * 100)}% заявок держится на brand-uplift — самое недоказуемое допущение. Нужен geo-holdout или MMM.")
    if pl["ltv_to_cac"] is not None and pl["ltv_to_cac"] < pl.get("hurdle_ltv_cac", 3):
        over = (pl["incremental_cpa"] / pl["max_cpa"]) if pl.get("max_cpa") else None
        out.append(
            f"LTV/CAC = {pl['ltv_to_cac']} на {pl['horizon_months']} мес "
            f"(норма ≥{pl.get('hurdle_ltv_cac', 3)}). Платить за клиента можно "
            f"до {nf(pl['max_cpa'])} ₽, фактически платим {nf(pl['incremental_cpa'])} ₽"
            + (f" — превышение в {over:.1f} раза." if over else ".")
            + f" Бюджет, который эта экономика выдерживает: {nf(pl['budget_at_max_cpa'])} ₽.")
    share = pl.get("marketing_share_of_costs")
    if pl.get("romi") is not None and share is not None and share < 0.5:
        out.append(
            f"ROMI СЧИТАЕТСЯ НЕ КО ВСЕМ ЗАТРАТАМ: в его знаменателе только маркетинг — "
            f"{nf(pl['marketing_cost'])} ₽ из {nf(pl['total_costs'])} ₽, то есть "
            f"{share:.0%}. IT-разработка, постоянные расходы и прочие разовые в него "
            f"не входят, хотя в NPV входят полностью. Отдача ко всем затратам здесь "
            f"{pl.get('roi_total')}, а не {pl['romi']} — на инвесткомитете нужна вторая "
            "цифра, первая для маркетингового разговора.")
    if pl["payback_months"] is None and pl.get("mode") != MODE_VALUE:
        out.append("Окупаемости на заданном горизонте нет.")
    # Deadweight там, где за клиента платят. Собственная методичка типа «Пилот или акция»
    # объявляет его обязательным, а движок не проверял ни разу — при том что каннибализацию
    # при нуле окликает всегда. Асимметрия ровно обратная тому, что написано.
    #
    # Триггер по поведению конфига, а не по `meta.type`: движок тип принципиально не читает,
    # и заводить исключение ради одной проверки значило бы сломать это правило. Платим
    # за клиента — значит вопрос «а взял бы он и так» стоит деньгами, независимо от того,
    # что человек выбрал в выпадающем списке.
    if (g(cfg.get("costs") or {}, "acq_cost_per_client") > 0
            and g(cfg.get("funnel") or {}, "deadweight") <= 0):
        out.append(
            "DEADWEIGHT = 0 ПРИ ВЫПЛАТЕ ЗА КЛИЕНТА: подарок или бонус получают все, кто "
            "сработал, включая тех, кто оформил бы и без него. «Скидка дала +40%» "
            "бессмысленно, если половина взяла бы и так — а при нуле модель считает, что "
            "не взял бы никто. Это самое дорогое допущение акции, и оно сейчас "
            "не задано, а не подтверждено.")
    if pl.get("acquisition_payout_total"):
        over = pl["gross_clients_paid"] - f["incremental_clients"]
        out.append(f"Выплаты идут за {nf(pl['gross_clients_paid'])} сработавших клиентов, а инкремент — "
                   f"только {nf(f['incremental_clients'])}. За {nf(over)} клиентов ({over/max(pl['gross_clients_paid'],1):.0%}) "
                   f"вы платите, ничего не получая: они пришли бы и без стимула или переехали со своих же продуктов. "
                   f"Фактическая выплата на одного ИНКРЕМЕНТАЛЬНОГО клиента — {nf(pl['payout_per_incremental_client'])} ₽.")
    # правила предметной области: оба флага есть на листе «Итог», в движке их не было.
    # Флаг только для продуктов, где риск вообще есть. Раньше он срабатывал на любом
    # продукте с положительной маржой — то есть на дебетовых картах и вкладах, где CoR
    # законно равен нулю. Постоянно кричащий ложный флаг обучает игнорировать все
    # остальные, а флаги здесь — главная защита от неверной цифры.
    # В режиме NPV продукта стоимость риска уже внутри присланного числа — там он тоже ложный.
    product = g(cfg.get("meta") or {}, "product", "")
    if (not is_value_mode(cfg) and product in CREDIT_PRODUCTS
            and g(cfg["unit_economics"], "cor_annual") <= 0
            and monthly_margin(cfg["unit_economics"], 10 ** 6) > 0):
        out.append("Cost of Risk = 0. Для кредитных продуктов это делает модель заведомо неверной: "
                   "без стоимости риска кредитка всегда выглядит прекрасно.")
    if g(cfg["funnel"], "cannibalization") <= 0:
        out.append("Каннибализация = 0. В банке новый продукт почти всегда ест старый — обоснуйте ноль.")
    return out

# B_minus — параметр, перенесённый с другого рынка или другой юрисдикции.
# Это не свой факт (грейд A) и не свой аналог (грейд B): рынок другой, доходы другие,
# проникновение продуктов другое. Но и не рыночный бенчмарк (C): цифра из валидированной
# модели, просто не той страны. Отдельный вес нужен, потому что при выводе продукта
# на новый рынок такими оказываются почти все параметры экономики сразу.
GRADE_W = {"A": 1.0, "B": 0.75, "B_minus": 0.6, "C": 0.5, "D": 0.2}
GRADE_LABEL = {"A": "свой факт", "B": "свой аналог", "B_minus": "перенос с другого рынка",
               "C": "рыночный бенчмарк", "D": "догадка"}

def confidence_index(cfg):
    c = cfg.get("confidence")
    if not c:
        return None
    tot = sum(c.get(k, 0) for k in GRADE_W)
    if tot == 0:
        return None
    idx = sum(c.get(k, 0) * w for k, w in GRADE_W.items()) / tot
    tier = "Высокая" if idx >= 0.8 else "Средняя" if idx >= 0.6 else "Низкая" if idx >= 0.4 else "Очень низкая"
    out = {"counts": c, "index": round(idx, 2), "tier": tier,
           "share_guesswork": round(c.get("D", 0) / tot, 2),
           "note": "Можно принимать инвестиционное решение." if idx >= 0.6
                   else "Решение о полном запуске принимать рано — сначала пилот или добор данных."}
    transfer = c.get("B_minus", 0)
    if transfer:
        out["transferred_params"] = transfer
        out["transfer_note"] = (
            f"{transfer} из {tot} допущений перенесены с другого рынка. На запуске "
            "в новой стране это самый недооценённый риск: параметры юнит-экономики "
            "выглядят обоснованными, потому что взяты из работающей модели, — "
            "но работающей на другом населении и другом уровне доходов. "
            "Проверять локальными данными в первую очередь их, а не догадки.")
    return out

def has_effect_beyond_acquisition(pl):
    """Есть ли у инициативы эффект помимо привлечения новых клиентов.

    Спутник, связанный клиент, растранзачивание и натуральный эффект работают на УЖЕ
    существующей базе и живут без единого нового клиента. Инициатива «разбудить спящих»
    или «помочь выйти из просрочки» — полноценный тип, особенно на зрелом рынке,
    где новых людей взять неоткуда.

    Список источников НЕ перечисляется руками: он выводится из margin_sources.
    Перечисление уже подводило — добавили натуральный эффект, забыли дописать сюда,
    и прогон с NPV +308 млн получил вердикт «нечего считать». Любой новый блок
    попадает под проверку сам, как только начинает давать маржу.
    """
    src = pl.get("margin_sources") or {}
    return any(v for k, v in src.items()
               if k not in ("own_product", "shares", "shares_note")
               and isinstance(v, (int, float)))


def currency_block(cfg, pl):
    """Ключевые суммы во второй валюте.

    Проект в другой стране считается в местной валюте — иначе каждое допущение
    приходится мысленно конвертировать, и там же появляются ошибки на порядок.
    А решение принимает комитет, который меряет в рублях. Нужны обе, из одного прогона.
    """
    m = cfg.get("meta", {})
    cur = m.get("currency") or "RUB"
    rate = g(m, "fx_to_rub")
    out = {"currency": cur}
    if cur == "RUB" or rate <= 0:
        return out
    out["fx_to_rub"] = rate
    out["in_rub"] = {k: round(pl[k] / rate) for k in
                     ("npv", "net_effect", "total_gross_margin", "total_costs", "marketing_cost")}
    if pl.get("incremental_cpa") is not None:
        out["in_rub"]["incremental_cpa"] = round(pl["incremental_cpa"] / rate)
    if pl.get("ltv_per_client") is not None:
        out["in_rub"]["ltv_per_client"] = round(pl["ltv_per_client"] / rate)
    return out


def analyze(cfg):
    """Полный результат по уже проверенному и сведённому конфигу.

    Отделено от main() ради сверки с Excel: тесту нужен тот же список флагов и тот же
    вердикт, что уходит в JSON, а не их копия, которая со временем разойдётся.
    """
    funnel, pl = evaluate(cfg)
    view = {k: v for k, v in funnel.items() if not k.startswith("_")}
    res = {"meta": cfg.get("meta", {}), "currency": currency_block(cfg, pl),
           "brief": brief_coverage(cfg), "pilot": cfg.get("_pilot"),
           "segments": cfg.get("_segments"), "funnel": view,
           "pl": {k: v for k, v in pl.items() if k != "monthly"}, "monthly": pl["monthly"],
           "scenarios": {}, "tornado": tornado(cfg, pl["npv"]),
           "rice": rice_score(cfg, funnel["_incremental_raw"], pl["npv"], pl),
           "confidence": confidence_index(cfg), "frk_ladder": frk_ladder(cfg),
           "flags": flags(cfg, view, pl), "validation": validate(cfg, view, pl)}
    for name, s in SCEN.items():
        ff, p = evaluate(apply_scenario(cfg, s))
        res["scenarios"][name] = {"incremental_clients": ff["incremental_clients"], "npv": p["npv"],
                                  "romi": p["romi"], "payback_months": p["payback_months"],
                                  "incremental_cpa": p["incremental_cpa"]}
    n = res["scenarios"]
    worst = min(v["npv"] for v in n.values())          # НЕ считаем, что пессимистичный = худший
    best = max(v["npv"] for v in n.values())
    # Инверсия сценариев: больше клиентов => хуже результат. Значит маржа клиента ниже
    # стоимости его привлечения, и масштабирование только увеличивает убыток.
    inverted = n["optimistic"]["npv"] < n["base"]["npv"] < n["pessimistic"]["npv"]
    if inverted:
        res["flags"].insert(0, "ИНВЕРСИЯ СЦЕНАРИЕВ: чем БОЛЬШЕ клиентов приводит инициатива, тем ХУЖЕ NPV. "
                               "Это значит, что маржа клиента меньше стоимости его привлечения, и каждый следующий "
                               "клиент увеличивает убыток. Ни один объём и ни один рост конверсии проблему не решат — "
                               "менять надо экономику: выплату, триггер или источник маржи.")
    # Нулевой инкремент — это не «не окупается», это «нечего считать». Разница
    # принципиальная: первое читается как решение по инициативе, второе — как пустой ввод.
    #
    # Но нулевой инкремент И нулевой эффект — разные вещи. Инициатива может вообще
    # не привлекать: разбудить спящих, удержать связанного клиента, поднять оборот
    # своей базе. На зрелом рынке это норма, а не вырожденный случай. Раньше движок
    # видел только `incremental_clients` и на прогоне с маржой в 19 млн ₽ отвечал
    # «нечего считать» — считал верно и говорил неправду.
    if funnel["incremental_clients"] <= 0 and not has_effect_beyond_acquisition(pl):
        res["verdict"] = {
            "npv_positive_in_scenarios": 0, "scenarios_inverted": False,
            "npv_worst": worst, "npv_best": best, "downgraded_by_confidence": False,
            "nothing_to_calculate": True,
            "recommendation_by_npv": "",
            "recommendation": "НЕЧЕГО СЧИТАТЬ — ни привлечения, ни эффекта на своей базе. "
                              "Модель не получила ни одного клиента и ни рубля маржи: "
                              "скорее всего не задан медиаплан или обнулилась конверсия. "
                              "Смотрите «Проверку вводных» — это не вердикт по инициативе, "
                              "а пустой прогон."}
        return res

    # Сценарии двигают конверсию, ставки юнит-экономики, отток и каннибализацию — и НЕ
    # трогают драйверы блоков эффекта: uplift растранзачивания, монетизацию натурального
    # эффекта, откат. У инициативы, которая целиком держится на своей базе, три сценария
    # дают одно и то же число, а вердикт при этом говорил «положительный NPV во всех
    # сценариях». Формально верно, содержательно — «мы посчитали одно число трижды».
    #
    # Здесь только честная подпись. Настоящая правка — распространить множители на драйверы
    # блоков эффекта — это пункт 50 бэклога целиком, и она тянет за собой пункт 13.
    scenarios_flat = best == worst
    by_npv = ("NO-GO по экономике юнита — масштаб делает только хуже, "
              "переработать выплату/триггер/источник маржи." if inverted and best <= 0
              else ("GO, но СЦЕНАРИИ ЭТУ ИНИЦИАТИВУ НЕ РАЗЛИЧАЮТ — все три дали одно "
                    "и то же число. Множители не касаются её драйверов: эффект держится "
                    "на блоках своей базы, а они в стресс-тест не входят. Это один "
                    "расчёт, а не три." if scenarios_flat
                    else "GO — положительный NPV во всех сценариях.") if worst > 0
              else "GO с контролем — база в плюсе, худший сценарий нет: зафиксировать stop-loss и чекпоинты." if n["base"]["npv"] > 0
              else "NO-GO / переработать — базовый сценарий не окупается.")
    res["verdict"] = {"npv_positive_in_scenarios": sum(1 for v in n.values() if v["npv"] > 0),
                      "scenarios_inverted": inverted, "npv_worst": worst, "npv_best": best,
                      "scenarios_flat": scenarios_flat,
                      "recommendation_by_npv": by_npv, "downgraded_by_confidence": False,
                      "recommendation": by_npv}
    # Индекс уверенности < 0,6 — вердикт не может быть «GO» ни при каком NPV.
    # Модель, у которой половина допущений из воздуха, даёт разброс шире собственного ответа:
    # красивый NPV здесь означает лишь удачную догадку, а не окупаемость.
    conf = res["confidence"]
    if conf and conf["index"] < 0.6 and by_npv.startswith("GO"):
        tot = sum(conf["counts"].get(k, 0) for k in GRADE_W)
        res["verdict"]["downgraded_by_confidence"] = True
        res["verdict"]["recommendation"] = (
            f"СНАЧАЛА ПИЛОТ — по деньгам вышло «{by_npv.split(' —')[0]}», но индекс уверенности "
            f"{conf['index']} (порог 0,6): из {tot} допущений {conf['counts'].get('D', 0)} — догадки, "
            f"{conf['counts'].get('C', 0)} — рыночные бенчмарки. Сначала добрать данные или пилот, "
            "затем пересчитать.")
        res["flags"].insert(0, f"ВЕРДИКТ ПОНИЖЕН: индекс уверенности {conf['index']} < 0,6. "
                               f"По NPV было «{by_npv.split(' —')[0]}». Цифра держится на допущениях "
                               "грейда C и D — подтвердите ключевые из них, иначе решение принимается "
                               "не по модели, а по догадке.")
    return res


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    res = analyze(load_config(src))
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
