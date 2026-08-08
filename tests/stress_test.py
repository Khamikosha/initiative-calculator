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

# реестр нужен с самого начала: возрастные правила и ОД проверяются вперемешку с движком
_ps = importlib.util.spec_from_file_location("params", ROOT / "src" / "params.py")
prm = importlib.util.module_from_spec(_ps); _ps.loader.exec_module(prm)

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

# perform-канал, помеченный как internal, тратит бюджет и даёт ноль: модель считает
# без ошибок и считает не то. Вскрылось на веб-форме, где тип канала по умолчанию
# подставлялся первым пунктом списка.
cfg = copy.deepcopy(BASE)
cfg["channels"] = [{"name": "Перформанс", "kind": "internal", "frk_tier": 4,
                    "budget": 25_000_000, "cpm": 400, "ctr": 0.006, "cr_to_app": 0.04}]
check("Ловится: канал тратит бюджет и не даёт ничего", has(cfg, "БЮДЖЕТ ВПУСТУЮ"))
cfg["channels"][0]["kind"] = "perform"
check("Правильный тип канала предупреждения не даёт", not has(cfg, "БЮДЖЕТ ВПУСТУЮ"))
cfg["channels"][0].update(kind="internal", budget=0)
check("Канал без бюджета не считается потраченным впустую", not has(cfg, "БЮДЖЕТ ВПУСТУЮ"))

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

# ---------------------------------------------------------------- 8. Возрастные сегменты
SEG_CASE = json.loads((CASES / "youth_card_segments.json").read_text(encoding="utf-8"))

def seg_cfg(**patch):
    c = copy.deepcopy(SEG_CASE)
    c["meta"].update(patch.pop("meta", {}))
    c["audience"].update(patch)
    return calc.resolve_segments(c)

# один сегмент со своими параметрами обязан дать ровно то же, что тот же конфиг без сегментов
one = copy.deepcopy(SEG_CASE)
youth = next(s for s in one["audience"]["segments"] if s["id"] == "youth")
one["audience"]["segments"] = [youth]
res_seg = run(calc.resolve_segments(one))
flat = copy.deepcopy(SEG_CASE)
flat["audience"] = {k: v for k, v in flat["audience"].items() if k != "segments"}
flat["audience"]["target_size"] = youth["target_size"]
flat["audience"]["baseline_organic_apps_m"] = youth["baseline_organic_apps_m"]
for k in ("approval_rate", "activation_rate", "cannibalization"):
    flat["funnel"][k] = youth[k]
for k in ("avg_balance", "nim_annual", "monthly_turnover", "interchange_rate",
          "cashback_rate", "servicing_cost_m", "ramp_months", "monthly_churn"):
    flat["unit_economics"][k] = youth[k]
res_flat = run(flat)
check("Один сегмент = тот же конфиг без сегментов",
      abs(res_seg[1]["npv"] - res_flat[1]["npv"]) < 1 and
      res_seg[0]["incremental_clients"] == res_flat[0]["incremental_clients"],
      f'NPV {res_seg[1]["npv"]:,} vs {res_flat[1]["npv"]:,}')

# ставки взвешиваются по своей базе, поэтому доходные компоненты маржи считаются ТОЧНО,
# а не приблизительно: средний_баланс × средний_NIM = среднему на клиента по сегментам
seg = calc.resolve_segments(copy.deepcopy(SEG_CASE))
rows = [r for r in seg["_segments"]["rows"] if r["effective_audience"] > 0]
w = [r["effective_audience"] * r["approval_rate"] * r["activation_rate"] for r in rows]
exact_nim = sum(x * r["avg_balance"] * r["nim_annual"] for x, r in zip(w, rows)) / sum(w)
exact_ic = sum(x * r["monthly_turnover"] * r["interchange_rate"] for x, r in zip(w, rows)) / sum(w)
u = seg["unit_economics"]
check("Взвешенный NIM точен: баланс × NIM = среднему по сегментам",
      abs(u["avg_balance"] * u["nim_annual"] - exact_nim) < 1e-6,
      f'{u["avg_balance"] * u["nim_annual"]:.4f} vs {exact_nim:.4f}')
check("Взвешенный интерчейндж точен",
      abs(u["monthly_turnover"] * u["interchange_rate"] - exact_ic) < 1e-6)
check("Веса клиентов суммируются в единицу",
      abs(sum(r["client_weight"] for r in seg["_segments"]["rows"]) - 1) < 0.001)

# возрастные ограничения продукта
credit = seg_cfg(meta={"product": "credit"})
excluded = [r for r in credit["_segments"]["rows"] if r["available_share"] == 0]
check("Кредитный продукт исключает детей и подростков",
      {r["id"] for r in excluded} == {"kids", "teens"},
      f'исключены {[r["id"] for r in excluded]}')
check("Исключение сегмента объясняется в валидации",
      any("СЕГМЕНТ ИСКЛЮЧЁН" in n for n in credit["_segments"]["notes"]))
check("Кредит: аудитория схлопывается до молодёжи",
      credit["audience"]["target_size"] == next(
          s["target_size"] for s in SEG_CASE["audience"]["segments"] if s["id"] == "youth"))

check("Карта детям доступна с 6 лет — 8 возрастов из 14",
      abs(calc.segment_availability("kids", "card")[0] - 8 / 14) < 1e-9)
check("Вклад детям доступен с рождения",
      calc.segment_availability("kids", "deposit") == (1.0, 0))
check("Брокерский счёт детям недоступен полностью",
      calc.segment_availability("kids", "broker")[0] == 0)
check("Подросткам подписка и лояльность доступны",
      calc.segment_availability("teens", "prime")[0] == 1.0 and
      calc.segment_availability("teens", "loyalty")[0] == 1.0)

deposit = seg_cfg(meta={"product": "deposit"})
check("Вклад детям: предупреждение про офис и третье лицо",
      any("офисе по свидетельству о рождении" in n for n in deposit["_segments"]["notes"]))
check("Дети + внутренние каналы: предупреждение про доступ в приложение",
      any("не имеют доступа в банковское приложение" in n
          for n in seg["_segments"]["notes"]))

bad = seg_cfg(segments=[{"id": "adults", "target_size": 1000}])
check("Неизвестный сегмент отлавливается",
      any("неизвестный сегмент" in n.lower() for n in bad["_segments"]["notes"]))

# ------------------------------------------------- 9. Строгая проверка конфига (BACKLOG 0.5)
def rejected(patch_fn, needle=None):
    """Конфиг после патча обязан быть отвергнут; при needle — ещё и с внятным объяснением."""
    cfg_ = copy.deepcopy(BASE); patch_fn(cfg_)
    try:
        calc.validate_config(cfg_)
        return False
    except calc.ConfigError as e:
        return needle is None or needle in str(e)

check("Опечатка в ключе не проходит молча",
      rejected(lambda c: c["funnel"].__setitem__("cannibalisation", c["funnel"].pop("cannibalization"))))
check("Опечатка сопровождается подсказкой правильного ключа",
      rejected(lambda c: c["funnel"].__setitem__("cannibalisation", 0.15), "cannibalization"))
check("Процент, введённый как 60 вместо 0.6, отвергается",
      rejected(lambda c: c["funnel"].__setitem__("approval_rate", 60), "долей"))
check("Неизвестный раздел отвергается",
      rejected(lambda c: c.__setitem__("unit_econimics", {}), "Неизвестный раздел"))
check("Неизвестный тип канала отвергается",
      rejected(lambda c: c["channels"][0].__setitem__("kind", "ooh"), "kind"))
check("Текст вместо числа отвергается",
      rejected(lambda c: c["unit_economics"].__setitem__("avg_balance", "45000"), "ожидалось число"))
check("Неизвестный сегмент отвергается на входе",
      rejected(lambda c: c["audience"].__setitem__("segments", [{"id": "adults"}]), "adults"))
check("Все рабочие кейсы проходят строгую проверку",
      all(calc.validate_config(json.loads(p.read_text(encoding="utf-8"))) is not None
          for p in sorted(CASES.glob("*.json"))))
check("saturation_factor удалён из движка — конфиг с ним отвергается",
      rejected(lambda c: c["channels"][0].__setitem__("saturation_factor", 0.5), "saturation_factor"))

# ------------------------------------------------- 10. Частота при обрезанном охвате (0.4)
_reach, _freq = calc.poisson_reach(360_000_000, 20_000_000)
check("Частота считается по обрезанному охвату, как в Excel",
      abs(_freq - (18.0 / 0.98)) < 1e-9,
      f"охват {_reach:.4f}, частота {_freq:.3f} (было 18.000 — расходилось с книгой)")

# ------------------------------------------------- 11. Флаги правил домена (0.2)
cfg = copy.deepcopy(BASE)
cfg["unit_economics"]["cor_annual"] = 0.0
cfg["funnel"]["cannibalization"] = 0.0
f_d, p_d = run(cfg)
fl = calc.flags(cfg, f_d, p_d)
check("Флаг «Каннибализация = 0» есть в движке", any("Каннибализация = 0" in x for x in fl))

# Флаг стоимости риска — только для кредитных продуктов. Раньше он срабатывал на любом
# продукте с положительной маржой, то есть на дебетовых картах и вкладах, где CoR
# законно равен нулю. Постоянно кричащий ложный флаг обучает игнорировать все остальные.
check("Флага CoR нет на дебетовом продукте — CoR там законно ноль",
      not any("Cost of Risk = 0" in x for x in fl))
cfg_cr = copy.deepcopy(BASE)
cfg_cr["meta"]["product"] = "credit"
cfg_cr["unit_economics"]["cor_annual"] = 0.0
f_cr, p_cr = run(cfg_cr)
check("На кредитном продукте с нулевым CoR флаг есть",
      any("Cost of Risk = 0" in x for x in calc.flags(cfg_cr, f_cr, p_cr)))
check("Незаданный продукт не молчит: три механизма не применились",
      any("ПРОДУКТ НЕ ЗАДАН" in m for m in calc.validate(cfg, f_d, p_d)))
check("С заданным продуктом этого сообщения нет",
      not any("ПРОДУКТ НЕ ЗАДАН" in m for m in calc.validate(cfg_cr, f_cr, p_cr)))
cfg2 = copy.deepcopy(BASE); cfg2["unit_economics"]["cor_annual"] = 0.15
f_c, p_c = run(cfg2)
check("Флага CoR нет, когда риск задан",
      not any("Cost of Risk = 0" in x for x in calc.flags(cfg2, f_c, p_c)))

# ------------------------------------------------- 12. Индекс уверенности и вердикт (0.1)
low = copy.deepcopy(BASE)
low["confidence"] = {"A": 0, "B": 0, "C": 2, "D": 10}
low["unit_economics"]["avg_balance"] = 90000            # выводим NPV в плюс
res_low = calc.analyze(calc.resolve_segments(low))
check("Индекс уверенности 0,25 при положительном NPV даёт «сначала пилот», а не GO",
      res_low["verdict"]["downgraded_by_confidence"] and
      not res_low["verdict"]["recommendation"].startswith("GO"),
      f'по NPV было «{res_low["verdict"]["recommendation_by_npv"].split(" —")[0]}», '
      f'итог «{res_low["verdict"]["recommendation"].split(" —")[0]}»')
check("Исходный вердикт по NPV сохраняется отдельным полем",
      res_low["verdict"]["recommendation_by_npv"].startswith("GO"))
check("Понижение вердикта объясняется первым флагом",
      res_low["flags"] and "ВЕРДИКТ ПОНИЖЕН" in res_low["flags"][0])

high = copy.deepcopy(BASE)
high["confidence"] = {"A": 10, "B": 2, "C": 0, "D": 0}
high["unit_economics"]["avg_balance"] = 90000
res_high = calc.analyze(calc.resolve_segments(high))
check("При хорошем индексе вердикт не понижается",
      not res_high["verdict"]["downgraded_by_confidence"] and
      res_high["verdict"]["recommendation"].startswith("GO"))

# ------------------------------- 13. Режим готовой ценности клиента (value_per_client)
VAL = json.loads((CASES / "product_kids_deposit.json").read_text(encoding="utf-8"))
v_res = calc.analyze(calc.resolve_segments(copy.deepcopy(VAL)))
check("Режим ценности отражён в результате", v_res["pl"]["mode"] == "value_per_client")
check("Валовая маржа = инкремент × ценность клиента",
      abs(v_res["pl"]["total_gross_margin"]
          - v_res["funnel"]["incremental_clients"] * v_res["pl"]["value_per_client"])
      < v_res["pl"]["total_gross_margin"] * 0.001,
      f'{v_res["pl"]["total_gross_margin"]:,} против '
      f'{v_res["funnel"]["incremental_clients"] * v_res["pl"]["value_per_client"]:,}')
check("LTV на клиента равен присланной ценности",
      abs(v_res["pl"]["ltv_per_client"] - v_res["pl"]["value_per_client"]) <= 1)
check("Срок окупаемости в режиме ценности не считается",
      v_res["pl"]["payback_months"] is None)
check("Отсутствие окупаемости объясняется, а не выдаётся за её отсутствие",
      any("НЕ считается" in m for m in v_res["validation"]) and
      not any("Окупаемости на заданном горизонте нет" in f for f in v_res["flags"]))

# отток внутри присланного числа: множитель оттока не должен применяться второй раз
_pess = calc.apply_scenario(calc.resolve_segments(copy.deepcopy(VAL)), calc.SCEN["pessimistic"])
_base = calc.resolve_segments(copy.deepcopy(VAL))
check("Пессимистичный сценарий бьёт по ценности ровно множителем маржи",
      abs(_pess["unit_economics"]["value_per_client"]
          - _base["unit_economics"]["value_per_client"] * 0.75) < 1)
check("Множитель оттока в режиме ценности не применяется — иначе пессимизм считался бы дважды",
      _pess["unit_economics"]["monthly_churn"] == _base["unit_economics"]["monthly_churn"])

check("Торнадо в режиме ценности крутит ценность, а не баланс с оборотом",
      {d["driver"] for d in v_res["tornado"]} >= {"Ценность клиента"} and
      not {d["driver"] for d in v_res["tornado"]} & {"Средний баланс", "Месячный оборот"})

_bad = copy.deepcopy(VAL); _bad["meta"]["horizon_months"] = 24
check("Расхождение горизонтов ценности и оценки ловится",
      any("несопоставимы" in m for m in
          calc.analyze(calc.resolve_segments(_bad))["validation"]))
check("Неизвестный режим отвергается",
      rejected(lambda c: c["unit_economics"].__setitem__("mode", "npv"), "mode"))
# Проверяем свойство, а не константу: числа сегментов меняются при каждом уточнении
# данных, а инвариант «сумма по сегментам = среднее × число клиентов» должен держаться.
_kk = calc.resolve_segments(json.loads(
    (CASES / "product_kids_key_deposit.json").read_text(encoding="utf-8")))
_rows = [r for r in _kk["_segments"]["rows"] if r.get("client_weight")]
_want = sum(r["client_weight"] * r["value_per_client"] for r in _rows)
_got = _kk["unit_economics"]["value_per_client"]
check("Ценность клиента взвешивается по числу клиентов, а не усредняется",
      abs(_got - _want) < 5 and
      min(r["value_per_client"] for r in _rows) < _got < max(r["value_per_client"] for r in _rows),
      "Детский Ключевой: " + " и ".join(
          f'{r["value_per_client"]:,.0f} ₽ (вес {r["client_weight"]:.0%})' for r in _rows)
      + f' → {_got:,.0f} ₽')

# ------------------------------- 14. Возрастные вилки именных продуктов
check("Детская карта — 6–13: подростки и молодёжь исключены",
      calc.segment_availability("teens", "kids_card")[0] == 0 and
      calc.segment_availability("youth", "kids_card")[0] == 0 and
      abs(calc.segment_availability("kids", "kids_card")[0] - 8 / 14) < 1e-9)
check("Детский Ключевой — 6–17: дети частично, подростки целиком, молодёжь нет",
      calc.segment_availability("teens", "kids_key_deposit")[0] == 1.0 and
      calc.segment_availability("youth", "kids_key_deposit")[0] == 0)
check("Молодёжный вклад — с 14: дети исключены",
      calc.segment_availability("kids", "youth_deposit")[0] == 0)
check("Родовая карта осталась без верхней границы",
      calc.segment_availability("youth", "card")[0] == 1.0)
# Правило банка: вклад ребёнку открывает родитель в офисе с рождения, а карту сам
# ребёнок получает с 6 лет. Поэтому у вклада и карты одного возраста разные пороги.
check("Детский вклад доступен с рождения, детская карта — с 6 лет",
      calc.PRODUCT_AGE["kids_deposit"][0] == 0 and calc.PRODUCT_AGE["kids_card"][0] == 6 and
      calc.segment_availability("kids", "kids_deposit")[0] == 1.0 and
      abs(calc.segment_availability("kids", "kids_card")[0] - 8 / 14) < 1e-9)
check("Рынок известен по всем возрастам линейки — пометки о неполноте нет",
      prm.segment_defaults("kids", "kids_deposit")["market_partial"] is None and
      prm.segment_defaults("kids", "kids_card")["market_partial"] is None)
check("Вклад с рождения видит рынок шире карты: 17 млн против 11 млн",
      prm.segment_defaults("kids", "kids_deposit")["target_size"] == 17_000_000 and
      prm.segment_defaults("kids", "kids_card")["target_size"] == 11_000_000)
# Самый незанятый кусок линейки: 6 млн человек, 50 тыс. клиентов. Проникновение
# на порядок ниже любого другого возраста, а детские вклады им доступны с рождения.
check("Возрасты 0–5 почти не охвачены — проникновение ниже 1%",
      sum(prm.AGE_CLIENTS[a] for a in range(6)) / prm.MARKET_BY_AGES[(0, 5)] < 0.01,
      f'{sum(prm.AGE_CLIENTS[a] for a in range(6)):,} клиентов из '
      f'{prm.MARKET_BY_AGES[(0, 5)]:,} — свободно 5,95 млн')

# ------------------------------- 15. Эффект в разных валютах (АКБ, ОД, AUM, PL)
check("«Итого» исходной таблицы ОД везде равно сумме БЕЗ ипотеки",
      all(abs(sum(prm.OD_BY_AGE[l][i] for l in prm.OD_BY_AGE if l != "ипотека")
              - tot) < 0.01
          for i, tot in enumerate([2514.4, 2917, 3024, 3292.6, 3132.6, 3401.4, 3836.5,
                                   4390.39, 5017.33, 5727.94, 6345.65])),
      "поэтому ипотека вынесена и по умолчанию не включается")
check("Ипотека по умолчанию не входит в ОД сегмента",
      prm.od_per_client("youth") < prm.od_per_client("youth", include_mortgage=True))
check("ОД сегмента считается только по его возрастам",
      prm.od_per_client("teens") < prm.od_per_client("youth"),
      f'подростки {prm.od_per_client("teens"):.0f} ₽ < молодёжь {prm.od_per_client("youth"):.0f} ₽')
check("ОД детей взят из средней суммы: детальной разбивки по линиям для них нет",
      abs(prm.od_per_client("kids") - prm.OD_KIDS_MONTHLY * 12) < 1,
      f'{prm.od_per_client("kids"):.0f} ₽/год = {prm.OD_KIDS_MONTHLY} ₽/мес × 12')
check("Разбивку по продуктовым линиям у детей не выдумываем",
      prm.od_per_client("kids", lines=["дебетовая карта"]) is None)

_e = calc.analyze(calc.resolve_segments(json.loads(
    (CASES / "product_youth_deposit.json").read_text(encoding="utf-8"))))["pl"]["effect"]
check("ОД за горизонт = клиенто-месяцы × годовой ОД / 12",
      abs(_e["od_total"] - _e["client_months"] * _e["od_annual_per_client"] / 12)
      < _e["od_total"] * 1e-4,          # od_total считается по неокруглённым величинам
      f'{_e["od_total"]:,} ₽ при {_e["client_months"]:,} клиенто-месяцах')
check("Дисконтированный ОД меньше недисконтированного",
      _e["od_discounted"] < _e["od_total"])
check("АКБ на конец горизонта меньше пика — отток работает",
      _e["akb_end"] < _e["akb_peak"])
check("PL в сводке эффекта совпадает с NPV расчёта",
      _e["pl_npv"] == calc.analyze(calc.resolve_segments(json.loads(
          (CASES / "product_youth_deposit.json").read_text(encoding="utf-8"))))["pl"]["npv"])

_e2 = calc.analyze(calc.resolve_segments(copy.deepcopy(BASE)))["pl"]["effect"]
check("AUM считается как пик активной базы × средний баланс",
      abs(_e2["aum_peak"] - _e2["akb_peak"] * _e2["funds_per_client"]) <= _e2["akb_peak"])
check("Без ОД в конфиге поля ОД пустые, а не нулевые",
      _e2["od_total"] is None and _e2["od_annual_per_client"] is None)
check("ОД — доход, а не маржа: он больше валовой маржи на тех же клиентах",
      _e["od_total"] > 0)

# ------------------------------- 16. Распределение клиентов по возрастам и проникновение
check("Доли внутри подсегментов суммируются в 100%",
      abs(prm.clients_in_segment("kids") - 5_050_000) < 1 and
      abs(prm.clients_in_segment("teens") - 4_495_500) < 1 and
      abs(prm.clients_in_segment("youth") - 9_300_000) < 1,
      f'дети {prm.clients_in_segment("kids"):,}, подростки {prm.clients_in_segment("teens"):,}, '
      f'молодёжь {prm.clients_in_segment("youth"):,}')
check("Проникновение растёт от детей к молодёжи",
      prm.penetration("kids") < prm.penetration("teens") < prm.penetration("youth"),
      f'{prm.penetration("kids"):.0%} → {prm.penetration("teens"):.0%} → {prm.penetration("youth"):.0%}')
check("ОД взвешен по клиентам, а не равномерно по годам",
      abs(prm.od_per_client("teens") - 2966) < 2 and abs(prm.od_per_client("youth") - 4555) < 2,
      f'подростки {prm.od_per_client("teens"):.0f} ₽ (равномерно было 2937), '
      f'молодёжь {prm.od_per_client("youth"):.0f} ₽ (было 4550)')
check("Клиентов в 13 лет в девять раз больше, чем в 6 — база копится с возрастом",
      prm.AGE_CLIENTS[13] / prm.AGE_CLIENTS[6] > 8)

_pen = calc.resolve_segments(json.loads(
    (CASES / "product_youth_deposit.json").read_text(encoding="utf-8")))
check("Высокое проникновение вызывает предупреждение",
      any("ПРОНИКНОВЕНИЕ" in n for n in _pen["_segments"]["notes"]))
check("Предупреждение различает привлечение и кросс-продажу",
      any("кросс-продаж" in n for n in _pen["_segments"]["notes"]))
check("Проникновение попадает в строку сегмента",
      all(r["penetration"] is not None for r in _pen["_segments"]["rows"]))
_nopen = copy.deepcopy(BASE)
check("Без числа клиентов проникновение не выдумывается",
      calc.resolve_segments(_nopen).get("_segments") is None or
      all(r.get("penetration") is None
          for r in calc.resolve_segments(_nopen)["_segments"]["rows"]))

# ------------------------------- 17. Пустой прогон не выдаётся за вердикт
# Вскрылось на первом же живом тесте: человек прошёл путь, не задав медиаплан, и получил
# «NO-GO — базовый сценарий не окупается» с окупаемостью 1 месяц. Ноль во всех клетках
# читался как оценка инициативы.
empty = copy.deepcopy(BASE)
empty["channels"] = []
empty["costs"] = {k: 0 for k in empty["costs"]}
empty["costs"]["campaign_months"] = 1
res_empty = calc.analyze(calc.resolve_segments(empty))
check("Пустой медиаплан даёт «нечего считать», а не «не окупается»",
      res_empty["verdict"].get("nothing_to_calculate") is True and
      "НЕЧЕГО СЧИТАТЬ" in res_empty["verdict"]["recommendation"])
check("Отсутствие каналов объясняется в проверке вводных",
      any("нет ни одного канала" in m for m in res_empty["validation"]))
check("При нулевом эффекте окупаемости нет — раньше показывалась «1 мес»",
      res_empty["pl"]["payback_months"] is None)
check("Окупаемость на живом кейсе по-прежнему считается",
      calc.analyze(calc.resolve_segments(json.loads(
          (CASES / "comms_push_base.json").read_text(encoding="utf-8")
      )))["pl"]["payback_months"] is not None)

# --- инициатива БЕЗ привлечения: эффект целиком на своей базе ------------------
# Раньше движок смотрел только на incremental_clients и на прогоне с маржой
# в 19 млн ₽ отвечал «нечего считать»: считал верно и говорил неправду.
# На зрелом рынке (проникновение 89%) это основной тип инициативы, а не вырожденный.
base_only = {
    "meta": {"name": "разбудить спящих", "type": "comms", "horizon_months": 24},
    "audience": {"target_size": 1000},
    "base_activation": {"eligible_clients": 1_000_000, "reach_share": 0.3,
                        "uplift_rate": 0.1, "measured_against_control": "да",
                        "turnover_before": 4000, "turnover_after": 12000,
                        "balance_before": 3000, "balance_after": 9000,
                        "decay_months": 12},
    "unit_economics": {"interchange_rate": 0.011, "cashback_rate": 0.003,
                       "nim_annual": 0.07, "monthly_churn": 0.03},
    "costs": {"campaign_months": 6}, "funnel": {},
}
res_bo = calc.analyze(calc.resolve_segments(calc.normalize(copy.deepcopy(base_only))))
check("Инициатива без привлечения не объявляется пустым прогоном",
      res_bo["verdict"].get("nothing_to_calculate") is not True,
      f'вердикт: {res_bo["verdict"]["recommendation"][:60]}')
check("Маржа растранзачивания попадает в результат",
      res_bo["pl"]["margin_sources"]["base_activation"] > 0)
check("Вердикт выносится по NPV, а не по числу новых клиентов",
      res_bo["funnel"]["incremental_clients"] == 0 and res_bo["pl"]["npv"] > 0
      and "НЕЧЕГО СЧИТАТЬ" not in res_bo["verdict"]["recommendation"])
check("Отсутствие привлечения объясняется, а не выдаётся за ошибку ввода",
      any("БЕЗ ПРИВЛЕЧЕНИЯ" in m for m in res_bo["validation"]) and
      not any("нет ни одного канала" in m for m in res_bo["validation"]))

# --- удержание откладывает уход, а не отменяет его ----------------------------
# `retained` раньше только рос: связанный клиент, удержанный на первом месяце,
# приносил полный доход все 36 месяцев и не уходил никогда.
def _linked_retention(churn_annual, months=36):
    cfg = {"linked_client": {"clients_per_linked": 1, "income_annual": 12000,
                             "attribution_share": 0, "cross_sell_uplift": 0,
                             "churn_annual": churn_annual, "churn_reduction": 1.0}}
    rows = calc.linked_stream(cfg, [100_000.0] * months)
    return rows[-1]["retained_cum"]


_ret_low = _linked_retention(0.07)
_ret_high = _linked_retention(0.60)
check("Удержанные не накапливаются без предела — они оттекают",
      _ret_low < 100_000 * (0.07 / 12) * 36,
      f"на конец {_ret_low:.0f} против {100_000 * (0.07/12) * 36:.0f} при вечной жизни")
check("Чем выше отток связанного, тем сильнее расходится с прежней формулой",
      _ret_high / (100_000 * (0.60 / 12) * 36) < _ret_low / (100_000 * (0.07 / 12) * 36),
      "на высоком оттоке старая формула завышала кратно")
check("Удержание всё же накапливает эффект, а не обнуляется",
      _ret_low > 0 and _ret_high > 0)

# --- сток и поток: накопленная база кончается, пополнение работает всегда --------
# На живом ФЭО накопленный отток в 1,97 млн подавался как источник 2 206 спасений
# ЕЖЕМЕСЯЧНО, хотя при таком темпе базы хватило бы на 892 месяца.
_sf = {
    "meta": {"name": "сток и поток", "type": "comms", "horizon_months": 24},
    "audience": {"target_size": 1000},
    "base_activation": {"eligible_clients": 1_000_000, "workout_months": 3,
                        "monthly_inflow": 50_000, "reach_share": 0.7,
                        "uplift_rate": 0.01, "measured_against_control": "да",
                        "turnover_before": 0, "turnover_after": 5000,
                        "balance_before": 0, "balance_after": 2000,
                        "decay_months": 12},
    "unit_economics": {"interchange_rate": 0.011, "cashback_rate": 0.003,
                       "nim_annual": 0.07, "monthly_churn": 0.03},
    "costs": {"campaign_months": 24}, "funnel": {}, "rice": {"hurdle_ltv_cac": 3.0},
}
_r_sf = calc.analyze(calc.resolve_segments(calc.normalize(copy.deepcopy(_sf))))
_b = _r_sf["pl"]["base_activation"]
check("Сток и поток считаются порознь, а не одной строкой",
      _b["activated_from_stock"] > 0 and _b["activated_from_flow"] > 0)
check("Сток кончается на заданном месяце и больше не пополняется",
      _b["stock_exhausted_month"] == 3,
      f'из стока {_b["activated_from_stock"]:,.0f}, из потока {_b["activated_from_flow"]:,.0f}')
check("Сток равен базе × конверсия, а не базе × конверсия × месяцы",
      abs(_b["activated_from_stock"] - 1_000_000 * 0.7 * 0.01) < 1,
      "иначе накопленную базу пересчитывали бы каждый месяц заново")
check("Поток за горизонт равен месячному пополнению × месяцы",
      abs(_b["activated_from_flow"] - 50_000 * 0.7 * 0.01 * 24) < 1)

_no_flow = copy.deepcopy(_sf)
_no_flow["base_activation"]["monthly_inflow"] = 0
_r_nf = calc.analyze(calc.resolve_segments(calc.normalize(_no_flow)))
check("Инициатива на одном стоке помечается: эффект кончится, затраты останутся",
      any("ТОЛЬКО СТОК" in m for m in _r_nf["validation"]))
check("Без пополнения активных к концу горизонта не остаётся",
      _r_nf["pl"]["base_activation"]["end_active_uplifted"] == 0)

# --- предельная цена контакта и предельный CPA -----------------------------------
# Метрика, которая закрывает вопрос «на какой бюджет»: не сколько платим,
# а сколько вообще МОЖНО платить.
check("Предельная цена контакта считается от ценности результата и конверсии",
      _b["max_cost_per_contact"] is not None and _b["contacts_per_activation"] > 0)
_expected = _b["value_per_activation"] * _b["conversion"] / 3.0
check("Предельная цена контакта = ценность × конверсия / норма",
      abs(_b["max_cost_per_contact"] - _expected) < 0.01,
      f'{_b["max_cost_per_contact"]} против {_expected:.3f}')
check("Экономика контакта доносится словами, а не только числом",
      any("ЦЕНА КОНТАКТА" in m for m in _r_sf["validation"]))

_frk = calc.analyze(calc.resolve_segments(json.loads(
    (CASES / "frk1_debit_card.json").read_text(encoding="utf-8"))))
_p = _frk["pl"]
check("Предельный CPA считается от LTV и нормы LTV/CAC",
      _p["max_cpa"] is not None and
      abs(_p["max_cpa"] - _p["ltv_per_client"] / _p["hurdle_ltv_cac"]) <= 1)
check("Бюджет, который выдерживает экономика, выводится, а не спрашивается",
      _p["budget_at_max_cpa"] is not None and _p["budget_at_max_cpa"] > 0)
def _config_error(cfg, needle):
    """Движок обязан ОТВЕРГНУТЬ конфиг с этой ошибкой, а не посчитать по нему."""
    try:
        calc.validate_config(copy.deepcopy(cfg))
        return False
    except calc.ConfigError as e:
        return needle in str(e)


# --- достижимость, части базы и поправка на отбор --------------------------------
# Три поправки из разбора живых ФЭО: техническая достижимость — не конверсия;
# спящая база неоднородна; средняя ценность не применяется к отобранной подвыборке.
_tr = copy.deepcopy(_sf)
_tr["base_activation"]["technically_reachable"] = 0.7
_r_tr = calc.analyze(calc.resolve_segments(calc.normalize(_tr)))
check("Техническая достижимость режет базу отдельным множителем",
      abs(_r_tr["pl"]["base_activation"]["activated_from_stock"]
          - _r_sf["pl"]["base_activation"]["activated_from_stock"] * 0.7) < 1)
check("Отсутствие достижимости помечается — теряется не отклик, а доставка",
      any("ДОСТИЖИМОСТЬ НЕ ЗАДАНА" in m for m in _r_sf["validation"]))

_pt = copy.deepcopy(_sf)
_pt["base_activation"]["parts"] = [
    {"name": "спят < 3 мес", "share": 0.4, "uplift_cap": 0.10},
    {"name": "спят > 12 мес", "share": 0.6, "uplift_cap": 0.002}]
_r_pt = calc.analyze(calc.resolve_segments(calc.normalize(_pt)))
# 1 млн × 0,7 × (0,4×0,01 + 0,6×0,002) = 1 млн × 0,7 × 0,0052
check("Потолок по частям базы применяется и к растранзачиванию",
      abs(_r_pt["pl"]["base_activation"]["activated_from_stock"]
          - 1_000_000 * 0.7 * (0.4 * 0.01 + 0.6 * 0.002)) < 1,
      f'{_r_pt["pl"]["base_activation"]["activated_from_stock"]:,.0f}')
check("Однородная спящая база помечается",
      any("СПЯЩАЯ БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ" in m for m in _r_sf["validation"]))
check("Доли частей базы обязаны давать единицу и здесь тоже",
      _config_error(dict(_pt, base_activation=dict(
          _pt["base_activation"],
          parts=[{"name": "a", "share": 0.4, "uplift_cap": 0.1}])), "parts"))


def _linked_retention_income(haircut):
    cfg = {"linked_client": {"clients_per_linked": 1, "income_annual": 12000,
                             "attribution_share": 0, "cross_sell_uplift": 0,
                             "churn_annual": 0.24, "churn_reduction": 1.0,
                             "selection_haircut": haircut}}
    return sum(r["income_retention"] for r in calc.linked_stream(cfg, [100_000.0] * 24))


check("Поправка на отбор режет доход от удержания пропорционально",
      abs(_linked_retention_income(0.6) - _linked_retention_income(1.0) * 0.6) < 1,
      "уходящий вовлечён хуже среднего — средняя ценность к нему неприменима")
check("Отсутствие поправки на отбор помечается",
      any("ПОПРАВКА НА ОТБОР" in m for m in calc.analyze(calc.resolve_segments(
          json.loads((CASES / "kids_card_uzbekistan.json").read_text(encoding="utf-8"))
      ))["validation"]))

# --- натуральный эффект: что меряем ≠ сколько это денег -------------------------
# На живом ФЭО «снижение просрочки на 3,65 млрд ₽» стояло в итоге как эффект.
# Реальные деньги — высвобожденный резерв минус цена инструментов помощи.
_nat = {
    "meta": {"name": "натуральный эффект", "type": "product_launch", "horizon_months": 12},
    "audience": {"target_size": 1000},
    "natural_effect": {
        "name": "Снижение просрочки", "unit": "₽ долга",
        "baseline": 1_000_000_000, "workout_months": 6, "monthly_inflow": 0,
        "uplift_rate": 0.10, "measured_against_control": "тест против контроля",
        "parts": [{"name": "ранние", "share": 0.4, "uplift_cap": 0.10, "monetization": 0.20},
                  {"name": "поздние", "share": 0.6, "uplift_cap": 0.02, "monetization": 0.80}],
        "adjustments": [{"name": "недополученный процент", "rate": -0.10}]},
    "unit_economics": {"monthly_churn": 0.02}, "funnel": {},
    "costs": {"campaign_months": 6}, "rice": {"hurdle_ltv_cac": 3.0},
}
_r_nat = calc.analyze(calc.resolve_segments(calc.normalize(copy.deepcopy(_nat))))
_n = _r_nat["pl"]["natural_effect"]
# ранние: 1 млрд × 0,4 × 0,10 = 40 млн;  поздние: 1 млрд × 0,6 × 0,02 = 12 млн
check("Потолок эффекта режет базу по частям, а не применяется ко всей",
      abs(_n["natural_total"] - 52_000_000) < 1,
      f'{_n["natural_total"]:,.0f} вместо 100 млн при однородной базе')
# в выводе ставки округлены до 4 знаков — сверяем с той же точностью
check("Ставка перевода взвешивается по частям базы",
      abs(_n["monetization_rate"] - round((40e6 * 0.20 + 12e6 * 0.80) / 52e6, 4)) < 1e-9,
      f'{_n["monetization_rate"]}')
check("Вычеты уменьшают перевод, а не натуральную величину",
      abs(_n["net_rate"] - (_n["monetization_rate"] - 0.10)) < 1e-6)
check("Натуральная величина и деньги — разные поля, их не перепутать",
      _n["natural_total"] != _n["money_total"] and
      abs(_n["money_total"] - _n["natural_total"] * _n["net_rate"]) < 5_000,
      f'{_n["natural_total"]:,} натуральных → {_n["money_total"]:,} ₽')
check("Разделение натурального и денежного доносится словами",
      any("НЕ деньги банка" in m for m in _r_nat["validation"]))

_nat_naive = copy.deepcopy(_nat)
_nat_naive["natural_effect"].pop("parts")
_nat_naive["natural_effect"].pop("adjustments")
_nat_naive["natural_effect"].pop("measured_against_control")
_r_naive = calc.analyze(calc.resolve_segments(calc.normalize(_nat_naive)))
check("Ставка перевода 1:1 помечается — именно здесь ФЭО завышают в разы",
      any("СТАВКА ПЕРЕВОДА НЕ ЗАДАНА" in m for m in _r_naive["validation"]))
check("Пустой список вычетов помечается: инструмент помощи не бесплатен",
      any("ВЫЧЕТОВ НЕТ" in m for m in _r_naive["validation"]))
check("Однородная база помечается: эффект возможен не везде",
      any("БАЗА СЧИТАЕТСЯ ОДНОРОДНОЙ" in m for m in _r_naive["validation"]))
check("Отсутствие контроля понижает доверие к блоку",
      any("БЕЗ КОНТРОЛЯ" in m for m in _r_naive["validation"]))

# Проверка «есть ли эффект» обязана видеть ЛЮБОЙ источник маржи, а не список руками.
# Уже подводило: добавили натуральный эффект, забыли дописать — прогон с NPV +308 млн
# получил вердикт «нечего считать».
check("Инициатива на одном натуральном эффекте не объявляется пустым прогоном",
      _r_nat["verdict"].get("nothing_to_calculate") is not True and
      _r_nat["funnel"]["incremental_clients"] == 0,
      f'вердикт: {_r_nat["verdict"]["recommendation"][:50]}')
check("Проверка эффекта выводится из margin_sources, а не из перечня",
      calc.has_effect_beyond_acquisition(
          {"margin_sources": {"own_product": 0, "будущий_блок": 1}}) is True,
      "любой новый источник попадает под проверку сам")

check("Норма LTV/CAC берётся из конфига, а не зашита",
      calc.analyze(calc.resolve_segments(
          dict(json.loads((CASES / "frk1_debit_card.json").read_text(encoding="utf-8")),
               rice={"reach": 0, "impact": 2, "confidence": 0.6, "effort": 12,
                     "hurdle_ltv_cac": 5.0})))["pl"]["max_cpa"] < _p["max_cpa"])

zero_conv = copy.deepcopy(BASE)
for ch in zero_conv["channels"]:
    ch["cr_to_app"] = 0; ch["brand_uplift"] = 0
check("Каналы есть, но конверсия нулевая — тоже ловится",
      any("заявок ноль" in m for m in
          calc.analyze(calc.resolve_segments(zero_conv))["validation"]))

# ------------------------------- 18. Отсутствие ОД не молчит
# Живой тест: на детском вкладе эффект в ОД остался пустым, и человек не мог понять,
# это ноль эффекта или отсутствие данных. Таблица ОД начинается с 14 лет.
no_od = json.loads((CASES / "product_kids_deposit.json").read_text(encoding="utf-8"))
for _s in no_od["audience"]["segments"]:
    _s.pop("operating_income_annual", None)
r_kids = calc.analyze(calc.resolve_segments(no_od))
check("Отсутствие данных по ОД объясняется, а не молчит",
      any("ДАННЫХ НЕТ" in m and "Дети" in m for m in r_kids["validation"]))
check("ОД пустой, а не нулевой — разницу видно",
      r_kids["pl"]["effect"]["od_total"] is None)
check("Остальные валюты при этом считаются",
      r_kids["pl"]["effect"]["akb_peak"] > 0 and r_kids["pl"]["effect"]["pl_npv"] != 0)

r_youth = calc.analyze(calc.resolve_segments(json.loads(
    (CASES / "product_youth_deposit.json").read_text(encoding="utf-8"))))
check("Там, где данные есть, лишнего сообщения нет",
      not any("ДАННЫХ НЕТ" in m for m in r_youth["validation"]) and
      r_youth["pl"]["effect"]["od_total"] > 0)

# ---------------------------------------------------------------- 19. Все кейсы считаются
for path in sorted(CASES.glob("*.json")):
    try:
        r = engine(path)
        check(f"Кейс {path.name}", True,
              f'инкремент {r["funnel"]["incremental_clients"]:,}, NPV {r["pl"]["npv"]:,}')
    except Exception as e:
        check(f"Кейс {path.name}", False, str(e))

# ------------------------------- Линт: форматирование чисел (BACKLOG 35)
# `.replace(",", " ")`, применённый ко ВСЕЙ строке сообщения, съедает обычные запятые
# вместе с разделителями разрядов: «за 892 клиента, которые пришли бы и так» → «клиента
# которые». Чинилось дважды и оба раза возвращалось, потому что защита держалась
# на памяти. Единственное законное место конструкции — тело nf().
import re as _re
_offenders = []
for _f in sorted(list((ROOT / "src").glob("*.py")) + list((ROOT / "web").glob("*.py"))):
    _in_nf = False
    for _i, _line in enumerate(_f.read_text(encoding="utf-8").splitlines(), 1):
        _stripped = _line.strip()
        if _stripped.startswith("def nf("):
            _in_nf = True
        elif _stripped.startswith("def ") and _in_nf:
            _in_nf = False
        if _stripped.startswith("#") or _in_nf:
            continue
        if _re.search(r'\.replace\(\s*","\s*,\s*"[  ]"\s*\)', _line):
            _offenders.append(f"{_f.name}:{_i}")
check("Форматирование чисел идёт только через nf()",
      not _offenders,
      f"ручной .replace найден в {', '.join(_offenders)}" if _offenders
      else "конструкция вне nf() не встречается")


# ---------------------------------------------------------------- вывод
print(f"\n{'='*78}\nПРОЙДЕНО: {len(ok)}   ПРОВАЛЕНО: {len(fail)}\n{'='*78}")
for t in ok:
    print("  ✓", t)
if fail:
    print("\n  ПРОВАЛЫ:")
    for t in fail:
        print("  ✗", t)
sys.exit(1 if fail else 0)
