#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движок расчёта эффекта инициатив (банк/финтех). Запуск: python3 calc.py config.json"""
import json, sys, math, copy, difflib

def g(d, k, default=0.0):
    v = d.get(k, default)
    return default if v is None else v

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
# в детскую карту и посчитала бы их по чужой ценности клиента.
PRODUCT_AGE = {
    # родовые категории
    "account": (0, None), "deposit": (0, None), "piggy_bank": (6, None), "card": (6, None),
    "savings": (14, None), "broker": (14, None), "prime": (14, None), "loyalty": (14, None),
    "credit": (18, None), "ecosystem": (18, None),
    # именные продукты линейки — рамки свои и у́же родовой категории
    "kids_card": (6, 13),
    "kids_deposit": (6, 13),
    "kids_key_deposit": (6, 17),
    "youth_deposit": (14, None),
}
PRODUCT_AGE_GATE = {k: lo for k, (lo, _) in PRODUCT_AGE.items()}   # только нижний порог
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
    # ценность клиента взвешивается по числу клиентов: суммарная ценность = Σ клиенты_i × ценность_i,
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
                f"клиенты банка. Незанятого рынка остаётся {free:,.0f} чел из "
                f"{r['target_size']:,.0f}. Модель считает охват против ВСЕЙ ЦА, поэтому если "
                "инициатива про привлечение НОВЫХ клиентов, инкремент завышен: возьмите "
                "target_size равным незанятому рынку. Если инициатива про кросс-продажу "
                "своей базе — всё верно, но растёт ОД, а не АКБ."
                .replace(",", " "))

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
             "horizon_months": (1, 600), "discount_rate_annual": (0.0, 2.0)},
    "audience": {"target_size": (0.0, 2e9), "baseline_organic_apps_m": (0.0, 1e9),
                 "halo_months": (0.0, 60.0), "halo_decay": _RATE, "segments": None},
    "funnel": dict(FUNNEL_KEYS),
    "unit_economics": {**UE_KEYS, "mode": None, "value_horizon_months": (1, 600)},
    "costs": {"production": _MONEY, "it_dev": _MONEY, "one_off_other": _MONEY,
              "opex_monthly": _MONEY, "campaign_months": (1, 600),
              "acq_cost_per_client": (0.0, 1e7)},
    "rice": {"reach": (0.0, 1e9), "impact": (0.0, 10.0), "confidence": _RATE, "effort": (0.0, 1e4)},
    "confidence": {k: (0, 1000) for k in "ABCD"},
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

# Два способа задать экономику клиента.
#   build_up        — собрать снизу вверх: баланс, оборот, ставки, отток, ramp. Даёт помесячную
#                     кривую, срок окупаемости и богатый торнадо, но требует десяти допущений.
#   value_per_client — взять готовую дисконтированную ценность клиента одним числом из
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
        if val is None:
            continue
        if not isinstance(val, dict):
            errs.append(f"Раздел «{sec}» должен быть объектом, получен {type(val).__name__}.")
        else:
            _check_keys(val, allowed, sec, errs)

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


def load_config(src):
    """Читает кейс, проверяет по схеме и сводит сегменты. Единственная точка входа."""
    cfg = json.load(open(src, encoding="utf-8")) if src else json.load(sys.stdin)
    return resolve_segments(validate_config(cfg))


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
    incremental = activated * (1 - g(f, "cannibalization")) * (1 - g(f, "deadweight"))
    return {"channels": ch_out, "target_audience": round(aud),
            "combined_reach_share": round(combined_reach, 4),
            "combined_reach_abs": round(combined_reach * aud),
            "total_impressions": round(total_impr),
            "avg_frequency": round(total_impr / (combined_reach * aud), 2) if combined_reach * aud > 0 else 0,
            "applications_direct": round(direct_apps), "applications_brand_lift": round(brand_apps),
            "applications_total": round(total_apps), "approved": round(approved),
            "activated": round(activated), "cannibalization_loss": round(activated - incremental),
            "incremental_clients": round(incremental),
            "_incremental_raw": incremental, "_activated_raw": activated, "_media_cost": media_cost}

def monthly_margin(u, month_index):
    """Contribution margin на одного активного клиента в месяц, ₽."""
    ramp = min(1.0, (month_index + 1) / max(g(u, "ramp_months", 1), 1))
    bal, turn = g(u, "avg_balance"), g(u, "monthly_turnover")
    rev = bal * g(u, "nim_annual") / 12.0 + turn * g(u, "interchange_rate") + g(u, "fee_income_m")
    cost = turn * g(u, "cashback_rate") + g(u, "servicing_cost_m") + bal * g(u, "cor_annual") / 12.0
    return (rev - cost) * ramp

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
    rows, cum, cum_disc, payback = [], 0.0, 0.0, None
    for m in range(h):
        cohort = inc / camp_m if m < camp_m else 0.0
        active = (rows[-1]["active_clients"] if rows else 0.0) * (1 - churn) + cohort
        if value_mode:
            # Ценность уже дисконтирована за свой горизонт ОТ МОМЕНТА привлечения и уже
            # включает отток, ramp и обслуживание. Строить помесячную маржу поверх неё —
            # значит посчитать жизнь клиента второй раз. Поэтому вся ценность когорты
            # признаётся в месяц привлечения, а дальше дисконтируется к нулевому месяцу.
            gm = cohort * value
        else:
            gm = active * monthly_margin(u, m)
        cost = ((media + acq_total) / camp_m if m < camp_m else 0.0)
        if m == 0:
            cost += g(k, "production") + g(k, "it_dev") + g(k, "one_off_other")
        cost += g(k, "opex_monthly")
        net = gm - cost; cum += net; cum_disc += net / ((1 + r_m) ** (m + 1))
        # В режиме ценности накопленный поток смешивает дисконтированную ценность
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
                     "costs": cost, "net": net, "cumulative": cum, "cumulative_discounted": cum_disc})
    total_gm = sum(r["gross_margin"] for r in rows); total_cost = sum(r["costs"] for r in rows)
    mkt_cost = media + g(k, "production") + acq_total
    effect = effect_summary(u, rows, r_m, total_gm - total_cost, cum_disc)
    return {"horizon_months": h, "effect": effect,
            "mode": MODE_VALUE if value_mode else MODE_BUILD_UP,
            "value_per_client": round(value) if value_mode else None,
            "gross_clients_paid": round(gross),
            "acquisition_payout_total": round(acq_total),
            "payout_per_incremental_client": round(acq_total / inc) if inc > 0 else None,
            "total_gross_margin": round(total_gm), "total_costs": round(total_cost),
            "net_effect": round(total_gm - total_cost), "npv": round(cum_disc), "payback_months": payback,
            "marketing_cost": round(mkt_cost),
            "romi": round((total_gm - mkt_cost) / mkt_cost, 2) if mkt_cost > 0 else None,
            "incremental_cpa": round(mkt_cost / inc) if inc > 0 else None,
            "ltv_per_client": round(total_gm / inc) if inc > 0 else None,
            "ltv_to_cac": round((total_gm / inc) / (mkt_cost / inc), 2) if inc > 0 and mkt_cost > 0 else None,
            "breakeven_clients": round(total_cost / (total_gm / inc)) if inc > 0 and total_gm > 0 else None,
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
        # Ценность клиента уже содержит отток внутри себя, поэтому множитель оттока НЕ
        # применяем: иначе пессимизм считался бы дважды — и через ценность, и через жизнь.
        # Пессимистичный сценарий здесь мягче, чем в build_up; если нужен жёстче —
        # присылайте пессимистичную ценность клиента отдельным числом.
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
    ("Выплата за привлечение", ("costs", "acq_cost_per_client"))]

# В режиме ценности драйверов юнит-экономики нет — вместо шести отдельных допущений
# одно число из чужой модели. Крутить его компоненты у нас нечем и незачем.
TORNADO_VALUE = [("Конверсия в заявку (все каналы)", "conv"),
                 ("Approval rate", ("funnel", "approval_rate")),
                 ("Activation rate", ("funnel", "activation_rate")),
                 ("Каннибализация", ("funnel", "cannibalization")),
                 ("Ценность клиента", ("unit_economics", "value_per_client")),
                 ("Deadweight (взяли бы и так)", ("funnel", "deadweight")),
                 ("Выплата за привлечение", ("costs", "acq_cost_per_client"))]


def tornado(cfg, base_npv, delta=0.25):
    out = []
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

def rice_score(cfg, incremental_clients, npv):
    r = cfg.get("rice")
    if not r:
        return None
    reach = g(r, "reach") or incremental_clients
    score = reach * g(r, "impact", 1) * g(r, "confidence", 0.5) / max(g(r, "effort", 1), 0.1)
    return {"reach": round(reach), "impact": g(r, "impact"), "confidence": g(r, "confidence"),
            "effort_person_months": g(r, "effort"), "rice_score": round(score, 1),
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

def validate(cfg, f, pl):
    """Проверки ВВОДНЫХ: ловят случаи, где модель считает без ошибок, но молча теряет часть эффекта."""
    out = list(cfg.get("_segments", {}).get("notes", []))
    h = int(g(cfg["meta"], "horizon_months", 24)); cm = int(max(g(cfg["costs"], "campaign_months", 1), 1))
    if h < cm:
        out.append(f"ОШИБКА ВВОДА: горизонт {h} мес короче кампании {cm} мес. "
                   f"В расчёт попадёт только {h}/{cm} медиабюджета и притока клиентов — цифры занижены. "
                   "Поставьте горизонт не меньше длительности кампании.")
    # Пустой медиаплан: считать нечего, но модель раньше молча возвращала нули и вердикт
    # «NO-GO — базовый сценарий не окупается», как будто это оценка инициативы.
    if not cfg.get("channels"):
        out.append("ОШИБКА ВВОДА: в плане нет ни одного канала. Показов, охвата и заявок "
                   "не будет, инкремент равен нулю — считать нечего. Это не оценка "
                   "инициативы, а пустой прогон: добавьте каналы на шаге «Допущения».")
    elif f["incremental_clients"] <= 0 and f["applications_total"] <= 0:
        out.append("ОШИБКА ВВОДА: каналы заданы, но заявок ноль. Проверьте бюджет, CPM "
                   "и конверсии: при нулевой конверсии или нулевом бюджете воронка пустая, "
                   "а результат выглядит как посчитанный.")
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
        out.append(f"РЕЖИМ: экономика задана готовой ценностью клиента "
                   f"({round(g(u, 'value_per_client')):,} ₽ за {vh} мес, дисконтированной). "
                   "Срок окупаемости в этом режиме НЕ считается: момент возврата денег "
                   "из одного числа не выводится. Нужна окупаемость — задавайте юнит-экономику "
                   "снизу вверх (mode = build_up).".replace(",", " "))
        if g(u, "value_per_client") <= 0:
            out.append("ОШИБКА ВВОДА: режим ценности клиента включён, но сама ценность = 0. "
                       "Вся валовая маржа обнулится, останутся одни затраты.")
        if vh != h:
            out.append(f"ОШИБКА ВВОДА: ценность посчитана за {vh} мес, а горизонт оценки {h} мес. "
                       "Затраты считаются на свой горизонт, а ценность приходит целиком — "
                       "цифры несопоставимы. Выровняйте горизонты.")
        # Отток НЕ влияет на NPV в этом режиме — он уже внутри присланной ценности. Но АКБ,
        # ОД и AUM считаются по живой кривой активной базы, и при нулевом оттоке клиент
        # не отваливается никогда: клиенто-месяцы, а с ними ОД и AUM, уходят в потолок.
        if g(u, "monthly_churn") <= 0 and (g(u, "operating_income_annual") or g(u, "avg_balance")):
            out.append("ВНИМАНИЕ: отток = 0. На NPV это не влияет (отток внутри ценности клиента), "
                       "но АКБ, ОД и AUM считаются по активной базе — при нулевом оттоке она "
                       "не убывает, и эти три цифры завышены. Задайте отток хотя бы 1–2% в месяц.")
        # Средний баланс из списка исключён намеренно: маржи он здесь не даёт, но остаётся
        # единственным источником AUM и дополнительного объёма средств.
        stale = [k for k in ("monthly_turnover", "nim_annual", "interchange_rate",
                             "cashback_rate", "servicing_cost_m", "cor_annual") if g(u, k)]
        if stale:
            out.append(f"ВНИМАНИЕ: в режиме ценности клиента не участвуют {', '.join(stale)} — "
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
                       f"{c['budget']:,} ₽ и не даёт ни одной заявки и ни капли brand-эффекта. "
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
    if pl["ltv_to_cac"] is not None and pl["ltv_to_cac"] < 3:
        out.append(f"LTV/CAC = {pl['ltv_to_cac']} на {pl['horizon_months']} мес (норма ≥3).")
    if pl["payback_months"] is None and pl.get("mode") != MODE_VALUE:
        out.append("Окупаемости на заданном горизонте нет.")
    if pl.get("acquisition_payout_total"):
        over = pl["gross_clients_paid"] - f["incremental_clients"]
        out.append(f"Выплаты идут за {pl['gross_clients_paid']:,} сработавших клиентов, а инкремент — "
                   f"только {f['incremental_clients']:,}. За {over:,} клиентов ({over/max(pl['gross_clients_paid'],1):.0%}) "
                   f"вы платите, ничего не получая: они пришли бы и без стимула или переехали со своих же продуктов. "
                   f"Фактическая выплата на одного ИНКРЕМЕНТАЛЬНОГО клиента — {pl['payout_per_incremental_client']:,} ₽.")
    # правила предметной области: оба флага есть на листе «Итог», в движке их не было.
    # В режиме ценности стоимость риска уже внутри присланного числа — флаг был бы ложным.
    if (not is_value_mode(cfg) and g(cfg["unit_economics"], "cor_annual") <= 0
            and monthly_margin(cfg["unit_economics"], 10 ** 6) > 0):
        out.append("Cost of Risk = 0. Для кредитных продуктов это делает модель заведомо неверной: "
                   "без стоимости риска кредитка всегда выглядит прекрасно.")
    if g(cfg["funnel"], "cannibalization") <= 0:
        out.append("Каннибализация = 0. В банке новый продукт почти всегда ест старый — обоснуйте ноль.")
    return out

GRADE_W = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.2}

def confidence_index(cfg):
    c = cfg.get("confidence")
    if not c:
        return None
    tot = sum(c.get(k, 0) for k in GRADE_W)
    if tot == 0:
        return None
    idx = sum(c.get(k, 0) * w for k, w in GRADE_W.items()) / tot
    tier = "Высокая" if idx >= 0.8 else "Средняя" if idx >= 0.6 else "Низкая" if idx >= 0.4 else "Очень низкая"
    return {"counts": c, "index": round(idx, 2), "tier": tier,
            "share_guesswork": round(c.get("D", 0) / tot, 2),
            "note": "Можно принимать инвестиционное решение." if idx >= 0.6
                    else "Решение о полном запуске принимать рано — сначала пилот или добор данных."}

def analyze(cfg):
    """Полный результат по уже проверенному и сведённому конфигу.

    Отделено от main() ради сверки с Excel: тесту нужен тот же список флагов и тот же
    вердикт, что уходит в JSON, а не их копия, которая со временем разойдётся.
    """
    funnel, pl = evaluate(cfg)
    view = {k: v for k, v in funnel.items() if not k.startswith("_")}
    res = {"meta": cfg.get("meta", {}), "segments": cfg.get("_segments"), "funnel": view,
           "pl": {k: v for k, v in pl.items() if k != "monthly"}, "monthly": pl["monthly"],
           "scenarios": {}, "tornado": tornado(cfg, pl["npv"]),
           "rice": rice_score(cfg, funnel["_incremental_raw"], pl["npv"]),
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
    if funnel["incremental_clients"] <= 0:
        res["verdict"] = {
            "npv_positive_in_scenarios": 0, "scenarios_inverted": False,
            "npv_worst": worst, "npv_best": best, "downgraded_by_confidence": False,
            "nothing_to_calculate": True,
            "recommendation_by_npv": "",
            "recommendation": "НЕЧЕГО СЧИТАТЬ — инкремент равен нулю. Модель не получила "
                              "ни одного клиента: скорее всего не задан медиаплан или "
                              "обнулилась конверсия. Смотрите «Проверку вводных» — "
                              "это не вердикт по инициативе, а пустой прогон."}
        return res

    by_npv = ("NO-GO по экономике юнита — масштаб делает только хуже, "
              "переработать выплату/триггер/источник маржи." if inverted and best <= 0
              else "GO — положительный NPV во всех сценариях." if worst > 0
              else "GO с контролем — база в плюсе, худший сценарий нет: зафиксировать stop-loss и чекпоинты." if n["base"]["npv"] > 0
              else "NO-GO / переработать — базовый сценарий не окупается.")
    res["verdict"] = {"npv_positive_in_scenarios": sum(1 for v in n.values() if v["npv"] > 0),
                      "scenarios_inverted": inverted, "npv_worst": worst, "npv_best": best,
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
