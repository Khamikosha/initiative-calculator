#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Стресс-тесты движка: инварианты, крайние случаи, монотонность, здравый смысл.

Запуск: make test   (или python3 tests/stress_test.py)
"""
import json, copy, subprocess, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src" / "calc.py"
CASES = ROOT / "cases"

spec = importlib.util.spec_from_file_location("calc", ENGINE)
calc = importlib.util.module_from_spec(spec); spec.loader.exec_module(calc)

BASE = json.loads((CASES / "frk1_debit_card.json").read_text(encoding="utf-8"))
ok, fail = [], []

def check(name, cond, detail=""):
    (ok if cond else fail).append(f"{name}" + (f" — {detail}" if detail else ""))

def run(cfg):
    return calc.evaluate(cfg)

def engine(case_path):
    out = subprocess.run([sys.executable, str(ENGINE), str(case_path)],
                         capture_output=True, text=True)
    return json.loads(out.stdout)

# ---------------------------------------------------------------- 1. Инварианты воронки
f, p = run(BASE)
check("Воронка монотонно сужается",
      f["applications_total"] >= f["approved"] >= f["activated"] >= f["incremental_clients"],
      f'{f["applications_total"]} ≥ {f["approved"]} ≥ {f["activated"]} ≥ {f["incremental_clients"]}')
check("Охват не превышает ЦА", f["combined_reach_abs"] <= f["target_audience"])
check("Охват каждого канала ≤ 98%", all(c["reach_share"] <= 0.98 for c in f["channels"]))
check("NPV меньше недисконтированного эффекта", p["npv"] < p["net_effect"] or p["net_effect"] < 0,
      f'NPV {p["npv"]:,} vs net {p["net_effect"]:,}')

# ---------------------------------------------------------------- 2. Крайние случаи
edge = [
    ("Нулевой медиабюджет", {"channels": []}),
    ("Нулевая аудитория", {"audience": {**BASE["audience"], "target_size": 0}}),
    ("Нулевая органика", {"audience": {**BASE["audience"], "baseline_organic_apps_m": 0}}),
    ("Approval = 0", {"funnel": {**BASE["funnel"], "approval_rate": 0}}),
    ("Каннибализация 100%", {"funnel": {**BASE["funnel"], "cannibalization": 1.0}}),
    ("Отток 100% в месяц", {"unit_economics": {**BASE["unit_economics"], "monthly_churn": 1.0}}),
    ("Ramp-up = 0", {"unit_economics": {**BASE["unit_economics"], "ramp_months": 0}}),
    ("Кампания 1 месяц", {"costs": {**BASE["costs"], "campaign_months": 1}}),
    ("Горизонт 1 месяц", {"meta": {**BASE["meta"], "horizon_months": 1}}),
    ("Горизонт 36 месяцев", {"meta": {**BASE["meta"], "horizon_months": 36}}),
    ("Ставка дисконта 0%", {"meta": {**BASE["meta"], "discount_rate_annual": 0}}),
    ("Выплата за привлечение 5000 ₽", {"costs": {**BASE["costs"], "acq_cost_per_client": 5000}}),
]
for name, patch in edge:
    cfg = copy.deepcopy(BASE); cfg.update(patch)
    try:
        f_, p_ = run(cfg)
        bad = [k for k, v in {**f_, **p_}.items()
               if isinstance(v, float) and (v != v or abs(v) == float("inf"))]
        check(f"Крайний случай: {name}", not bad, f"NaN/Inf в {bad}" if bad else
              f'инкремент {f_["incremental_clients"]:,}, NPV {p_["npv"]:,}')
    except Exception as e:
        check(f"Крайний случай: {name}", False, f"ИСКЛЮЧЕНИЕ {type(e).__name__}: {e}")

# ---------------------------------------------------------------- 3. Монотонность
prev, mono = None, True
for mult in (0.25, 0.5, 1.0, 2.0, 4.0):
    cfg = copy.deepcopy(BASE)
    for c in cfg["channels"]:
        c["budget"] = c.get("budget", 0) * mult
    f_, _ = run(cfg)
    if prev is not None and f_["incremental_clients"] < prev:
        mono = False
    prev = f_["incremental_clients"]
check("Рост бюджета не уменьшает клиентов", mono)

prev, mono = None, True
for h in (12, 24, 36, 48):
    cfg = copy.deepcopy(BASE); cfg["meta"]["horizon_months"] = h
    _, p_ = run(cfg)
    if prev is not None and p_["npv"] < prev - 1:
        mono = False
    prev = p_["npv"]
check("Рост горизонта не уменьшает NPV при положительной марже", mono)

f1, _ = run(BASE)
cfg2 = copy.deepcopy(BASE)
for c in cfg2["channels"]:
    c["budget"] = c.get("budget", 0) * 2
f2, _ = run(cfg2)
check("Убывающая отдача на охватной части",
      f2["applications_brand_lift"] < f1["applications_brand_lift"] * 2,
      f'brand-заявки {f1["applications_brand_lift"]:,} → {f2["applications_brand_lift"]:,} при x2 бюджете')

# ---------------------------------------------------------------- 4. Сценарии
res = engine(CASES / "frk1_debit_card.json")
sc = res["scenarios"]
check("Сценарии упорядочены при положительной юнит-экономике",
      sc["pessimistic"]["npv"] < sc["base"]["npv"] < sc["optimistic"]["npv"])
check("Множитель конверсий применяется ровно один раз",
      abs(sc["pessimistic"]["incremental_clients"] / sc["base"]["incremental_clients"] - 0.6 * 0.70 / 0.85) < 0.01,
      f'отношение {sc["pessimistic"]["incremental_clients"]/sc["base"]["incremental_clients"]:.3f}, ожидали 0.494')
check("Вердикт берёт худший сценарий, а не пессимистичный по имени",
      "npv_worst" in res["verdict"] and
      res["verdict"]["npv_worst"] == min(v["npv"] for v in sc.values()))

# инверсия сценариев ловится на реферальном кейсе (отрицательная юнит-экономика)
ref = engine(CASES / "referral_youth_deposit.json")
check("Инверсия сценариев детектируется", ref["verdict"]["scenarios_inverted"] is True)
check("При инверсии вердикт указывает на экономику юнита, а не на объём",
      "экономик" in ref["verdict"]["recommendation"].lower())
check("Флаг инверсии стоит первым", ref["flags"] and "ИНВЕРСИЯ" in ref["flags"][0])

# ---------------------------------------------------------------- 5. Лестница ФРК
lad = res["frk_ladder"]["levels"]
check("Лестница монотонна по бюджету", all(lad[i]["budget"] < lad[i+1]["budget"] for i in range(len(lad)-1)))
check("Лестница монотонна по клиентам", all(lad[i]["incremental_clients"] <= lad[i+1]["incremental_clients"] for i in range(len(lad)-1)))
check("Предельный NPV = разница NPV соседних уровней",
      all(abs(lad[i+1]["marginal_npv"] - (lad[i+1]["npv"] - lad[i]["npv"])) < 1 for i in range(len(lad)-1)))
check("Оптимальный уровень = максимум NPV",
      res["frk_ladder"]["optimal_tier"] == max(lad, key=lambda x: x["npv"])["frk_tier"])
cfg = copy.deepcopy(BASE)
for c in cfg["channels"]:
    c["frk_tier"] = 4
check("Один уровень у всех каналов — лестница не строится", calc.frk_ladder(cfg) is None)

# ---------------------------------------------------------------- 6. Валидация вводных
def has(cfg, needle):
    f_, p_ = run(cfg)
    return any(needle in v for v in calc.validate(cfg, f_, p_))

cfg = copy.deepcopy(BASE); cfg["meta"]["horizon_months"] = 1
check("Ловится: горизонт короче кампании", has(cfg, "короче кампании"))
cfg = copy.deepcopy(BASE); cfg["audience"]["target_size"] = 0
check("Ловится: нулевая ЦА", has(cfg, "ЦА = 0"))
cfg = copy.deepcopy(BASE); cfg["audience"]["baseline_organic_apps_m"] = 0
check("Ловится: охватные каналы при нулевой органике", has(cfg, "органический поток"))
cfg = copy.deepcopy(BASE); cfg["unit_economics"]["monthly_churn"] = 0
check("Ловится: нулевой отток", has(cfg, "отток = 0"))
check("Ловится: насыщение канала", has(copy.deepcopy(BASE), "НАСЫЩЕНИЕ"))

# ---------------------------------------------------------------- 7. Экономическая логика
cfg = copy.deepcopy(BASE); cfg["unit_economics"]["cor_annual"] = 0.15
_, p_cor = run(cfg)
check("Cost of Risk снижает NPV", p_cor["npv"] < p["npv"],
      f'{p["npv"]:,} → {p_cor["npv"]:,} при CoR 15%')

cfg = copy.deepcopy(BASE); cfg["funnel"]["deadweight"] = 0.5
f_dw, _ = run(cfg)
check("Deadweight 50% срезает инкремент вдвое",
      abs(f_dw["incremental_clients"] - f["incremental_clients"] * 0.5) < 2)

cfg = copy.deepcopy(BASE); cfg["costs"]["it_dev"] = 500_000_000
_, p_it = run(cfg)
check("IT-разработка бьёт по NPV, но не по ROMI",
      p_it["npv"] < p["npv"] and p_it["romi"] == p["romi"])

# выплаты за привлечение платятся за ВСЕХ сработавших, а не за инкремент
cfg = copy.deepcopy(BASE); cfg["costs"]["acq_cost_per_client"] = 1000
f_a, p_a = run(cfg)
check("Выплаты считаются от активированных, а не от инкремента",
      abs(p_a["acquisition_payout_total"] - f_a["activated"] * 1000) < 1000,
      f'выплаты {p_a["acquisition_payout_total"]:,} при {f_a["activated"]:,} сработавших '
      f'и {f_a["incremental_clients"]:,} инкрементальных')
check("Выплата на инкрементального клиента выше номинала при каннибализации",
      p_a["payout_per_incremental_client"] > 1000)

# ---------------------------------------------------------------- 8. Все кейсы считаются
for path in sorted(CASES.glob("*.json")):
    try:
        r = engine(path)
        check(f"Кейс {path.name}", True,
              f'инкремент {r["funnel"]["incremental_clients"]:,}, NPV {r["pl"]["npv"]:,}')
    except Exception as e:
        check(f"Кейс {path.name}", False, str(e))

# ---------------------------------------------------------------- вывод
print(f"\n{'='*78}\nПРОЙДЕНО: {len(ok)}   ПРОВАЛЕНО: {len(fail)}\n{'='*78}")
for t in ok:
    print("  ✓", t)
if fail:
    print("\n  ПРОВАЛЫ:")
    for t in fail:
        print("  ✗", t)
sys.exit(1 if fail else 0)
