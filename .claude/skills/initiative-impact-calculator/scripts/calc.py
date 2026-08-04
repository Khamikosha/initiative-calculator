#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движок расчёта эффекта инициатив (банк/финтех). Запуск: python3 calc.py config.json"""
import json, sys, math, copy

def g(d, k, default=0.0):
    v = d.get(k, default)
    return default if v is None else v

def poisson_reach(impressions, audience):
    """Reach(1+) = 1 - e^(-lambda), где lambda = средняя частота контакта на человека в ЦА."""
    if audience <= 0 or impressions <= 0:
        return 0.0, 0.0
    lam = impressions / audience
    reach = 1.0 - math.exp(-lam)
    freq = lam / reach if reach > 0 else 0.0
    return min(reach, 0.98), freq

def build_funnel(cfg):
    aud = g(cfg["audience"], "target_size")
    base_apps_m = g(cfg["audience"], "baseline_organic_apps_m")
    f = cfg["funnel"]
    ch_out, media_cost = [], 0.0
    direct_apps = 0.0; brand_uplift_weighted = 0.0
    reach_complement = 1.0; total_impr = 0.0

    for c in cfg.get("channels", []):
        kind = c.get("kind", "perform"); budget = g(c, "budget")
        sat = g(c, "saturation_factor", 1.0); media_cost += budget
        if kind == "internal":
            impr = g(c, "reach_abs")
            apps = impr * g(c, "response_rate") * g(c, "cr_to_app") * sat
            r_share, freq = (min(impr / aud, 0.98) if aud else 0.0), 1.0
            direct_apps += apps
        else:
            cpm = g(c, "cpm")
            impr = (budget / cpm * 1000.0) if cpm > 0 else g(c, "impressions")
            r_share, freq = poisson_reach(impr, aud)
            if kind == "perform":
                apps = impr * g(c, "ctr") * g(c, "cr_to_app") * sat
                direct_apps += apps
            else:  # brand — эффект через прирост органики, а не прямая атрибуция
                apps = 0.0
                eff = min(1.0, freq / g(c, "effective_freq_target", 3.0))
                brand_uplift_weighted += g(c, "brand_uplift") * r_share * eff * sat
        total_impr += impr; reach_complement *= (1.0 - r_share)
        ch_out.append({"name": c.get("name", kind), "kind": kind, "budget": round(budget),
                       "impressions": round(impr), "reach_share": round(r_share, 4),
                       "freq": round(freq, 2), "applications": round(apps),
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
    rows, cum, cum_disc, payback = [], 0.0, 0.0, None
    for m in range(h):
        cohort = inc / camp_m if m < camp_m else 0.0
        active = (rows[-1]["active_clients"] if rows else 0.0) * (1 - churn) + cohort
        gm = active * monthly_margin(u, m)
        cost = ((media + acq_total) / camp_m if m < camp_m else 0.0)
        if m == 0:
            cost += g(k, "production") + g(k, "it_dev") + g(k, "one_off_other")
        cost += g(k, "opex_monthly")
        net = gm - cost; cum += net; cum_disc += net / ((1 + r_m) ** (m + 1))
        if payback is None and cum >= 0:
            payback = m + 1
        rows.append({"month": m + 1, "active_clients": active, "gross_margin": gm,
                     "costs": cost, "net": net, "cumulative": cum, "cumulative_discounted": cum_disc})
    total_gm = sum(r["gross_margin"] for r in rows); total_cost = sum(r["costs"] for r in rows)
    mkt_cost = media + g(k, "production") + acq_total
    return {"horizon_months": h, "gross_clients_paid": round(gross),
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

def tornado(cfg, base_npv, delta=0.25):
    out = []
    for label, path in TORNADO:
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
    out = []
    h = int(g(cfg["meta"], "horizon_months", 24)); cm = int(max(g(cfg["costs"], "campaign_months", 1), 1))
    if h < cm:
        out.append(f"ОШИБКА ВВОДА: горизонт {h} мес короче кампании {cm} мес. "
                   f"В расчёт попадёт только {h}/{cm} медиабюджета и притока клиентов — цифры занижены. "
                   "Поставьте горизонт не меньше длительности кампании.")
    if g(cfg["audience"], "target_size") <= 0:
        out.append("ОШИБКА ВВОДА: размер ЦА = 0. Охват, частота и весь brand-эффект молча обнуляются, "
                   "но прямые заявки остаются — результат выглядит правдоподобно и при этом неверен.")
    if g(cfg["audience"], "baseline_organic_apps_m") <= 0 and any(
            c.get("kind") == "brand" for c in cfg.get("channels", [])):
        out.append("ОШИБКА ВВОДА: в плане есть охватные каналы (ТВ/наружка), но органический поток заявок = 0. "
                   "Brand-эффект считается как прирост к органике, поэтому весь охватный бюджет даёт ровно 0 заявок.")
    if g(cfg["unit_economics"], "monthly_churn") <= 0:
        out.append("ВНИМАНИЕ: отток = 0, клиент живёт вечно. LTV и NPV завышены — поставьте хотя бы 1–2% в месяц.")
    # насыщение канала: добавочный бюджет уже не работает
    for c in f["channels"]:
        if c["kind"] == "brand" and c["reach_share"] >= 0.97 and c["freq"] >= 4.5:
            out.append(f"НАСЫЩЕНИЕ: канал «{c['name']}» упёрся в потолок — охват {c['reach_share']:.0%}, "
                       f"частота {c['freq']}. Дополнительный бюджет сюда НЕ даёт дополнительных заявок вообще. "
                       "Деньги эффективнее перебросить в другой канал или расширить ЦА.")
        if c["kind"] == "perform" and c["reach_share"] >= 0.97 and c["freq"] >= 8:
            out.append(f"ВНИМАНИЕ: «{c['name']}» — частота {c['freq']} при полном охвате. Модель считает отклик "
                       "линейно от показов, но на практике здесь уже работает выгорание: занизьте CTR на 15–30%.")
    return out

def flags(f, pl):
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
    if pl["payback_months"] is None:
        out.append("Окупаемости на заданном горизонте нет.")
    if pl.get("acquisition_payout_total"):
        over = pl["gross_clients_paid"] - f["incremental_clients"]
        out.append(f"Выплаты идут за {pl['gross_clients_paid']:,} сработавших клиентов, а инкремент — "
                   f"только {f['incremental_clients']:,}. За {over:,} клиентов ({over/max(pl['gross_clients_paid'],1):.0%}) "
                   f"вы платите, ничего не получая: они пришли бы и без стимула или переехали со своих же продуктов. "
                   f"Фактическая выплата на одного ИНКРЕМЕНТАЛЬНОГО клиента — {pl['payout_per_incremental_client']:,} ₽.")
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

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = json.load(open(src, encoding="utf-8")) if src else json.load(sys.stdin)
    funnel, pl = evaluate(cfg)
    view = {k: v for k, v in funnel.items() if not k.startswith("_")}
    res = {"meta": cfg.get("meta", {}), "funnel": view,
           "pl": {k: v for k, v in pl.items() if k != "monthly"}, "monthly": pl["monthly"],
           "scenarios": {}, "tornado": tornado(cfg, pl["npv"]),
           "rice": rice_score(cfg, funnel["_incremental_raw"], pl["npv"]),
           "confidence": confidence_index(cfg), "frk_ladder": frk_ladder(cfg), "flags": flags(view, pl), "validation": validate(cfg, view, pl)}
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
    res["verdict"] = {"npv_positive_in_scenarios": sum(1 for v in n.values() if v["npv"] > 0),
                      "scenarios_inverted": inverted, "npv_worst": worst, "npv_best": best,
                      "recommendation": ("NO-GO по экономике юнита — масштаб делает только хуже, "
                                         "переработать выплату/триггер/источник маржи." if inverted and best <= 0
                                         else "GO — положительный NPV во всех сценариях." if worst > 0
                                         else "GO с контролем — база в плюсе, худший сценарий нет: зафиксировать stop-loss и чекпоинты." if n["base"]["npv"] > 0
                                         else "NO-GO / переработать — базовый сценарий не окупается.")}
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
