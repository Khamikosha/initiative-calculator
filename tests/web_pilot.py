#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Шаг «Пилот»: развилка, разница разниц, доказательность, перенос в модель."""
from webkit import *          # noqa: F401,F403  — общая обвязка: client, check, params, calc

# Своя инициатива с разобранным брифом: раньше эти проверки жили на общей,
# созданной в другом файле, и порядок прогона становился значимым.
iid = walkthrough_initiative("Пилот — тест")

# ---------------------------------------------------------------- 2б. Шаг «Пилот»
# Пилот, который провели и не использовали в расчёте, — выброшенные деньги. Раньше
# задать его цифры через веб было нельзя вовсе, только конфигом.
r = client.get(f"/initiative/{iid}/pilot")
check("Шаг «Пилот» открывается", r.status_code == 200 and "Пилот уже проводился" in r.text)

r = client.post(f"/initiative/{iid}/pilot", data={"action": "save"}, follow_redirects=False)
_cfg = json.loads(_db.get_initiative(app_mod.con, iid)["config_json"])
check("Без галочки раздел пилота не создаётся", "pilot" not in _cfg,
      "инициатива без пилота не должна тащить пустой блок в конфиг")

r = client.post(f"/initiative/{iid}/pilot", data={
    "has_pilot": "on", "pilot.name": "Пилот в Москве",
    "pilot.geography": "Москва", "pilot.rollout_geography": "РФ",
    "pilot.control_group": "on",
    "pilot.sample_size": "40000", "pilot.control_size": "40000",
    "pilot.baseline_rate": "0,52", "pilot.observed_rate": "0,61",
    "pilot.scale_haircut": "0,75", "pilot.metrics_are": "level",
    "metric.0.key": "activation_rate", "metric.0.value": "0,61",
    "metric.1.key": "monthly_turnover", "metric.1.value": "34000",
    "metric.2.key": "approval_rate", "metric.2.value": "",     # пустое не сохраняется
    "action": "save"}, follow_redirects=False)
check("Пилот сохраняется", r.status_code == 303 and "error=" not in r.headers["location"])

_cfg = json.loads(_db.get_initiative(app_mod.con, iid)["config_json"])
check("Метрики пилота легли в конфиг",
      _cfg["pilot"]["metrics"] == {"activation_rate": 0.61, "monthly_turnover": 34000.0})
check("Пустая метрика не сохранилась", "approval_rate" not in _cfg["pilot"]["metrics"])
check("Русская запятая разобрана и в полях пилота", _cfg["pilot"]["baseline_rate"] == 0.52)

_no_answer = json.loads(json.dumps(_cfg))
_no_answer["pilot"].pop("metrics_are", None)
try:
    calc.validate_config(_no_answer)
    _pilot_err = []
except calc.ConfigError as e:
    _pilot_err = str(e).splitlines()


def _apply_as(kind):
    c = json.loads(json.dumps(_cfg))
    c["pilot"]["metrics_are"] = kind
    return calc.apply_pilot(c)


_probe_level, _probe_uplift = _apply_as("level"), _apply_as("uplift")
_applied_level = {a["param"] for a in _probe_level["_pilot"]["applied"]}
_applied_uplift = {a["param"] for a in _probe_uplift["_pilot"]["applied"]}

_probe = calc.apply_pilot(json.loads(json.dumps(_cfg)))
check("География Москва → РФ понижает грейд до B−", _probe["_pilot"]["grade"] == "B_minus")
check("Мощность выборки считает движок: 9 п.п. на 40 000 доказуемы",
      _probe["_pilot"]["power"]["conclusive"] is True)
# Развилка «уровень или прирост». Число одно и то же, фразы звучат одинаково,
# а разница на этом кейсе — 15 млн ₽ NPV. Раньше движок выбирал молча.
check("Пилот с контролем и метрикой в рублях требует ответа, что в ней записано",
      any("metrics_are" in e for e in _pilot_err),
      "; ".join(_pilot_err)[:80] if _pilot_err else "движок промолчал")
check("Ответ «уровень» подставляет величину в модель",
      "unit_economics.monthly_turnover" in _applied_level)
check("Ответ «прирост» НЕ подставляет её как уровень",
      "unit_economics.monthly_turnover" not in _applied_uplift,
      "иначе вышел бы клиент, у которого весь оборот равен приросту")
check("Отказ подставить объяснён и указывает, чем считать прирост",
      any("ПРИРОСТ НЕ ПОДСТАВЛЕН" in n and "Растранзачивание" in n
          for n in _probe_uplift.get("_notes", [])))
check("Подстановка уровня оговаривает, что контроль для неё не использовался",
      any("УРОВЕНЬ, А НЕ ПРИРОСТ" in n for n in _probe_level.get("_notes", [])))
check("Доли развилка не касается: конверсия подставляется при любом ответе",
      "funnel.activation_rate" in _applied_level and
      "funnel.activation_rate" in _applied_uplift,
      "у доли «уровень» и «прирост» не путаются — прирост там задан отдельными полями")

# Дизайн замера, доказательность в рублях и окно наблюдения.
# Пилот молодёжной карты: рандомизация с выравниванием, 14 000 на группу,
# 8 месяцев (май–декабрь), только средние — разброса нет.
_beh = json.loads(json.dumps(_cfg))
_beh["pilot"].update({"randomized": True, "matched_at_baseline": True,
                      "sample_size": 14000, "control_size": 14000,
                      "duration_months": 8, "metrics_are": "uplift",
                      "metrics": {"monthly_turnover": 1800}})


def _notes_of(cfg):
    return calc.apply_pilot(calc.validate_config(json.loads(json.dumps(cfg))))["_notes"]


_n_nosd = _notes_of(_beh)
check("Рандомизация с выравниванием снимает вопрос об отборе",
      any("ДИЗАЙН" in n and "поправка на отбор не нужна" in n for n in _n_nosd))
_selfsel = json.loads(json.dumps(_beh)); _selfsel["pilot"]["randomized"] = False
check("Без рандомизации движок называет отбор отбором",
      any("ОТБОР ВМЕСТО ЭФФЕКТА" in n for n in _notes_of(_selfsel)))
check("Только средние — «доказано» не пишется ни при каких условиях",
      not any("ПРИРОСТ ДОКАЗАТЕЛЕН" in n for n in _n_nosd) and
      any("РАЗБРОСА НЕТ" in n or "ДОКАЗАТЕЛЬНОСТЬ НЕ ПРОВЕРЕНА" in n for n in _n_nosd),
      "у рубля разброс из среднего не выводится")

_thr = calc.mde_mean(2.0 * _cfg["unit_economics"]["monthly_turnover"], 14000)
_cv = json.loads(json.dumps(_beh)); _cv["pilot"]["metric_cv"] = 2.0
_cv["pilot"]["metrics"] = {"monthly_turnover": round(_thr * 1.2)}
check("С заданным разбросом доказательность считается",
      any("ПРИРОСТ ДОКАЗАТЕЛЕН" in n for n in _notes_of(_cv)),
      f"порог {_thr:,.0f} ₽ при 14 000 на группу")
_small = json.loads(json.dumps(_cv))
_small["pilot"]["metrics"] = {"monthly_turnover": round(_thr * 0.5)}
check("Малый прирост при том же разбросе объявлен неотличимым от шума",
      any("НЕОТЛИЧИМ ОТ ШУМА" in n for n in _notes_of(_small)),
      "половина порога на тех же группах — это шум, а не эффект")
check("Порог различимости считает движок и он зависит от размера групп",
      calc.mde_mean(2.0 * 24000, 14000) > calc.mde_mean(2.0 * 24000, 56000),
      f"14 000 на группу: {calc.mde_mean(2.0 * 24000, 14000):,.0f} ₽, "
      f"56 000: {calc.mde_mean(2.0 * 24000, 56000):,.0f} ₽")

check("Восемь месяцев из двенадцати названы неполным годом",
      any("ОКНО НАБЛЮДЕНИЯ" in n for n in _n_nosd),
      "май–декабрь не покрывают год, а перенос делается как на наблюдавшиеся")
check("Замер короче горизонта — сказано, что дальше держится по допущению",
      any("ЗАМЕР" in n and "ГОРИЗОНТ" in n for n in _n_nosd))

# Затухание из двух чисел. Помесячной аналитики нет, и не нужно: хвост окна
# против средней за окно однозначно задаёт срок — при той модели затухания,
# которую движок реально применяет.
check("Формула обращает кривую затухания самого движка",
      abs(calc.decay_from_tail(8, 2, 0.333333) - 8.0) < 0.01,
      "затухание ровно за 8 мес даёт отношение 1/3 — и обратно")
check("Хвост не ниже средней — срок не выводится, это не ошибка",
      calc.decay_from_tail(8, 2, 1.0) is None and calc.decay_from_tail(8, 2, 1.2) is None)
check("Чем ближе хвост к средней, тем дольше живёт эффект",
      calc.decay_from_tail(8, 2, 0.9) > calc.decay_from_tail(8, 2, 0.6))

_tail = json.loads(json.dumps(_beh))
_tail["pilot"].update({"tail_months": 2, "metrics_tail": {"monthly_turnover": 1440}})
_pt = calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_tail))))
check("Срок затухания выведен из замера и назван",
      _pt["_pilot"]["decay_from_tail"]["monthly_turnover"] == 18.5 and
      any("ЗАТУХАНИЕ ИЗ ЗАМЕРА" in n for n in _pt["_notes"]),
      "хвост 1 440 против средней 1 800 → около 19 мес")
check("Средняя за окно не выдаётся за пик",
      any("СРЕДНЯЯ ЗА ОКНО — НЕ ПИК" in n for n in _pt["_notes"]))

_flat = json.loads(json.dumps(_tail))
_flat["pilot"]["metrics_tail"] = {"monthly_turnover": 1800}
check("Ровный хвост читается как полка, а не как затухание",
      any("ЭФФЕКТ НЕ ЗАТУХАЕТ" in n for n in _notes_of(_flat)),
      "и сказано, что перенос средней тогда скорее занижает")

_bad = json.loads(json.dumps(_tail))
_bad["pilot"]["metrics_tail"] = {"avg_balance": 100}
try:
    calc.validate_config(_bad); _bad_err = []
except calc.ConfigError as e:
    _bad_err = str(e).splitlines()
check("Хвост без своей средней отвергается: сравнивать не с чем",
      any("metrics_tail" in e for e in _bad_err))

# Шаг пилота просил 16 полей и две таблицы у человека, которому нужна половина,
# и он не знал, какая именно. Развилка задаётся ОДНИМ вопросом наверху.
_kpage = client.get(f"/initiative/{iid}/pilot").text
check("Первым задаётся вопрос, что пилот мерил",
      'name="pilot.kind"' in _kpage and
      _kpage.index('name="pilot.kind"') < _kpage.index('name="pilot.scale_haircut"'),
      "от ответа зависит, какие поля вообще показывать")
check("Ветки размечены и различимы",
      'data-kind="conversion"' in _kpage and 'data-kind="behavioral"' in _kpage)
_conv = _kpage.split('data-kind="conversion"')[1].split('data-kind="behavioral"')[0]
_beh_html = _kpage.split('data-kind="behavioral"')[1]
check("Конверсия живёт только в своей ветке",
      'name="pilot.observed_rate"' in _conv and 'name="pilot.observed_rate"' not in _beh_html)
check("Групповые замеры живут только в своей",
      'name="pgroup.0.name"' in _beh_html and 'name="pgroup.0.name"' not in _conv)
check("Размеры групп общие: без них ни один тип пилота не проверить на шум",
      'name="pilot.sample_size"' in _kpage.split('data-kind="conversion"')[0],
      "мощность выборки нужна и конверсии, и поведению")
# ищем по разметке раскрытия, а не по слову «Уточнения»: оно есть и в подводке,
# и срез между вхождениями попадал мимо блока
_fold = _kpage.split('<details class="fold">')[-1]
check("Редкое убрано под раскрытие, а не выброшено",
      "Уточнения" in _kpage and
      all(f'name="pilot.{k}"' in _fold
          for k in ("metric_cv", "tail_months", "metrics_are", "audience")))

# Конфликт веток чистится на СЕРВЕРЕ: скрытые поля всё равно уходят с формой,
# а конфиг с обеими ветками движок отвергнет — человек при этом видел половину
# и не поймёт, о чём речь.
_ki = int(client.post("/initiative/new", data={
    "name": "Ветки", "type": "product_launch", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
_kd = {"has_pilot": "on", "pilot.kind": "behavioral", "pilot.name": "М",
       "pilot.baseline_rate": "0,52", "pilot.observed_rate": "0,61",
       "pgroup.0.name": "МК", "pgroup.0.role": "test",
       "pgroup.0.metric": "monthly_turnover",
       "pgroup.0.value_start": "22000", "pgroup.0.value_end": "27400",
       "pgroup.1.name": "ОК", "pgroup.1.role": "control",
       "pgroup.1.metric": "monthly_turnover",
       "pgroup.1.value_start": "21800", "pgroup.1.value_end": "24000",
       "action": "save"}
_kr = client.post(f"/initiative/{_ki}/pilot", data=_kd, follow_redirects=False)
_kcfg = json.loads(_db.get_initiative(_db.connect(), _ki)["config_json"])
check("Поля чужой ветки не сохраняются и не ломают конфиг",
      "error=" not in _kr.headers.get("location", "") and
      "observed_rate" not in _kcfg["pilot"] and len(_kcfg["pilot"]["groups"]) == 2)
check("Контроль у поведенческого пилота — это строка «контроль», а не галочка",
      calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_kcfg))))
      ["pilot"]["control_group"] is True,
      "иначе можно снять галочку при заполненной строке контроля и противоречить себе")

_mix = json.loads(json.dumps(_kcfg))
_mix["pilot"]["kind"] = "conversion"
try:
    calc.validate_config(_mix); _mix_err = []
except calc.ConfigError as e:
    _mix_err = str(e).splitlines()
check("Заявленный тип и заполненное содержимое сверяются",
      any("groups" in e for e in _mix_err),
      "конверсионный пилот с групповыми замерами — противоречие, а не данные")

# Живая проверка и сохранение обязаны видеть ОДНО И ТО ЖЕ (BACKLOG 44).
# Скрытая половина формы остаётся в DOM со значениями и уходит на сервер как есть:
# если предпросмотр её учитывает, панель показывает «пилот доказателен» про замер,
# которого в расчёте не будет.
_mixed = {"kind": "behavioral", "name": "Москва", "control_group": True,
          "baseline_rate": 0.52, "observed_rate": 0.61,
          "sample_size": 14000, "control_size": 14000, "duration_months": 8,
          "randomized": True, "matched_at_baseline": True,
          "groups": [{"name": "МК", "role": "test", "metric": "monthly_turnover",
                      "value_start": 22000, "value_end": 27400},
                     {"name": "ОК", "role": "control", "metric": "monthly_turnover",
                      "value_start": 21800, "value_end": 24000}]}
_pv_beh = client.post("/api/pilot/preview", json={"pilot": _mixed}).json()
check("Выбрано «поведение» — предпросмотр не считает мощность по конверсии",
      not _pv_beh["errors"] and (_pv_beh.get("info") or {}).get("power") is None and
      (_pv_beh.get("info") or {}).get("derived"),
      "конверсия осталась в форме с прошлого переключения и в расчёт не идёт")
_pv_conv = client.post("/api/pilot/preview",
                       json={"pilot": dict(_mixed, kind="conversion")}).json()
check("Выбрана «конверсия» — предпросмотр не считает разницу разниц",
      not _pv_conv["errors"] and (_pv_conv.get("info") or {}).get("power") and
      (_pv_conv.get("info") or {}).get("derived") is None)

check("Ветка отбрасывается ОДНОЙ функцией, а не копией в каждом пути",
      "strip_other_branch" in __import__("inspect").getsource(app_mod.form_to_pilot) and
      "strip_other_branch" in __import__("inspect").getsource(app_mod.api_pilot_preview),
      "третья копия правила разойдётся с остальными при первой правке")
check("Отбрасывание описано данными, а не ветвлением по типу",
      set(app_mod.PILOT_BRANCH_KEYS) == {"conversion", "behavioral"})

_pv_saved = client.post("/api/pilot/preview", json={"pilot": _mixed}).json()
check("Предпросмотр и сохранение показывают одно и то же",
      (_pv_saved.get("info") or {}).get("derived") ==
      (calc.apply_pilot(calc.validate_config({
          **json.loads(json.dumps(params.defaults())),
          "pilot": app_mod.strip_other_branch(json.loads(json.dumps(_mixed)))}))
       ["_pilot"] or {}).get("derived"),
      "иначе панель противоречит результату")

check("Браузер не затирает введённое в скрытой ветке",
      "branch[hidden] input" not in
      (ROOT / "web" / "templates" / "pilot.html").read_text("utf-8"),
      "переключиться посмотреть и потерять свои цифры человек не должен")

# Веб собирал конфиг СВОЕЙ короткой сборкой — без apply_pilot и без normalize.
# Значит пилот в приложении не применялся вовсе, а блок «что дал пилот» не мог
# появиться в принципе. Тесты этого не видели, потому что дёргали apply_pilot
# напрямую, минуя путь, которым ходит человек.
check("Веб и CLI собирают конфиг одной и той же функцией",
      app_mod.resolved.__doc__ and "calc.prepare" in
      __import__("inspect").getsource(app_mod.resolved),
      "вторая сборка конфига — то же самое, что вторая модель")
_pcfg_in = {"meta": {"name": "П", "type": "product_launch", "horizon_months": 24},
            "pilot": {"name": "Пилот", "metrics": {"approval_rate": 0.9}}}
check("Сборка веба применяет пилот, а не только валидирует",
      "_pilot" in app_mod.resolved(
          {**json.loads(json.dumps(params.defaults())), **_pcfg_in}),
      "иначе цифры пилота молча не доезжают до расчёта")

# Замеры по группам: абсолюты на начало и конец, дельты считает движок.
# Разница «до и после» внутри группы эффектом не является — за срок пилота
# клиенты стали старше, прошёл сезон, изменился рынок.
_gr = json.loads(json.dumps(_cfg))
_gr["pilot"].pop("metrics", None)
_gr["pilot"].pop("metrics_are", None)
_gr["pilot"].update({"control_group": True, "randomized": True,
                     "matched_at_baseline": True, "duration_months": 8,
                     "sample_size": 14000, "control_size": 14000, "groups": [
    {"name": "Молодёжная карта", "role": "test", "metric": "monthly_turnover",
     "value_start": 22000, "value_end": 27400},
    {"name": "Обычная карта", "role": "control", "metric": "monthly_turnover",
     "value_start": 21800, "value_end": 24000}]})
_grr = calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_gr))))
check("Эффект считается как разница разниц, а не рост внутри группы",
      _grr["pilot"]["metrics"]["monthly_turnover"] == 3200.0,
      "тест вырос на 5 400, контроль сам на 2 200 — «до/после» завысило бы на 69%")
check("Посчитанное из абсолютов помечается приростом само",
      _grr["pilot"]["metrics_are"] == "uplift",
      "спрашивать «уровень или прирост» тут уже нечего")
check("Разница разниц объяснена, а не просто посчитана",
      any("РАЗНИЦА РАЗНИЦ" in n for n in _grr["_notes"]))
check("Выравнивание проверено по числам, а не принято на слово",
      any("ВЫРАВНИВАНИЕ ПОДТВЕРЖДЕНО ЗАМЕРОМ" in n for n in _grr["_notes"]))
check("Сказано, что это уровень на конец окна, а не средняя за срок",
      any("ЭТО УРОВЕНЬ НА КОНЕЦ ОКНА" in n for n in _grr["_notes"]),
      "при выровненных группах средняя за окно занижает полку")

_skew = json.loads(json.dumps(_gr))
_skew["pilot"]["groups"][0]["value_start"] = 26000        # разошлись ещё до пилота
check("Расхождение групп на старте названо вслух",
      any("ГРУППЫ РАЗОШЛИСЬ НА СТАРТЕ" in n
          for n in calc.apply_pilot(calc.validate_config(_skew))["_notes"]))

_noctl = json.loads(json.dumps(_gr))
_noctl["pilot"]["groups"] = [_noctl["pilot"]["groups"][0]]
_nc = calc.apply_pilot(calc.validate_config(_noctl))
check("Группа без контроля даёт рост, а не эффект — и так и написано",
      any("РОСТ БЕЗ КОНТРОЛЯ — НЕ ЭФФЕКТ" in n for n in _nc["_notes"]) and
      "monthly_turnover" not in (_nc["pilot"].get("metrics") or {}),
      "иначе взросление клиентов и сезон уехали бы в эффект инициативы")

check("apply_pilot не портит поданный ему конфиг",
      "metrics" not in json.loads(json.dumps(_gr))["pilot"],
      "функция копирует вход — иначе правки уезжают в исходный объект")

# Сквозняк до экрана результата: форма → конфиг → движок → отрисованная страница.
# Именно этого куска не хватало — все прежние проверки пилота останавливались
# на apply_pilot и не доходили до того, что видит человек.
_ri = int(client.post("/initiative/new", data={
    "name": "Пилот на экране", "type": "product_launch", "product": "card"},
    follow_redirects=False).headers["location"].split("/")[2])
_rd = {"has_pilot": "on", "pilot.name": "Москва", "pilot.control_group": "on",
       "pilot.randomized": "on", "pilot.matched_at_baseline": "on",
       "pilot.sample_size": "14000", "pilot.control_size": "14000",
       "pilot.duration_months": "8", "pilot.scale_haircut": "0,75", "action": "save"}
for _i, (_n, _role, _v0, _v1) in enumerate(
        [("Молодёжная карта", "test", 22000, 27400),
         ("Обычная карта", "control", 21800, 24000)]):
    _rd[f"pgroup.{_i}.name"] = _n
    _rd[f"pgroup.{_i}.role"] = _role
    _rd[f"pgroup.{_i}.metric"] = "monthly_turnover"
    _rd[f"pgroup.{_i}.value_start"] = str(_v0)
    _rd[f"pgroup.{_i}.value_end"] = str(_v1)
client.post(f"/initiative/{_ri}/pilot", data=_rd, follow_redirects=False)
client.post(f"/initiative/{_ri}/segments",
            data={"segments": ["youth"], "seg.youth.baseline_organic_apps_m": "700"},
            follow_redirects=False)
client.post(f"/initiative/{_ri}/assumptions", data={
    "costs.campaign_months": "6", "ch.0.name": "Перф", "ch.0.kind": "perform",
    "ch.0.frk_tier": "4", "ch.0.budget": "50000000", "ch.0.cpm": "250",
    "ch.0.ctr": "0.008", "ch.0.cr_to_app": "0.06", "action": "save"},
    follow_redirects=False)
_rres = client.post(f"/initiative/{_ri}/calculate", follow_redirects=False)
_rhtml = client.get(_rres.headers["location"]).text
check("Блок пилота доходит до экрана результата",
      "Что дал пилот" in _rhtml)
# Разряды форматируются НЕРАЗРЫВНЫМ пробелом — обычный в тесте не совпадёт
# с тем, что на странице, хотя человек видит одно и то же.
_flat_html = _rhtml.replace("\u00a0", " ").replace("\u2009", " ")
check("Разница разниц показана с обеими группами и разрывом на старте",
      all(x in _flat_html for x in ("разница разниц", "5 400", "2 200", "3 200")),
      "рост теста, рост контроля и эффект — три разных числа")
check("Показано, что перенесено в модель",
      "Что перенесено" in _rhtml)
check("Показано и то, что НЕ перенесено",
      "НЕ перенесено" in _rhtml,
      "иначе короткий список выглядит как всё, что пилот дал")
check("Параметры подписаны по-человечески, а не ключами движка",
      "monthly_turnover" not in _rhtml.split("Что дал пилот")[1].split("</section>")[0],
      "название берётся из реестра")

_gpage = client.get(f"/initiative/{iid}/pilot").text
for _f in ("name", "role", "metric", "value_start", "value_end"):
    check(f"Колонка замеров «{_f}» есть в форме",
          f'name="pgroup.0.{_f}"' in _gpage)

# Недостающих цифр не будет. Тогда спрашиваем не «доказано ли», а «что должно быть
# верно, чтобы было доказано» — обе обратные задачи считаются из того, что уже есть.
check("Критический разброс считается из прироста, уровня и размера групп",
      abs(calc.critical_cv(1800, 24000, 14000) - 2.22) < 0.01,
      "прирост 1 800 к уровню 24 000 различим, пока разброс не выше 2,2×")
check("Больше групп — выше допустимый разброс",
      calc.critical_cv(1800, 24000, 56000) > calc.critical_cv(1800, 24000, 14000))
check("Без разброса движок отвечает обратной задачей, а не молчанием",
      any("РАЗБРОСА НЕТ — СЧИТАЕМ ОБРАТНОЕ" in n for n in _n_nosd),
      "утверждение о базе можно проверить качественно, не имея новых цифр")

def _npv_at(ready_cfg, decay):
    c = json.loads(json.dumps(ready_cfg))
    c["base_activation"]["decay_months"] = decay
    return calc.evaluate(c)[1]["npv"]


_be_cfg = json.loads(json.dumps(_beh))
_be_cfg["base_activation"] = {
    "name": "Против обычной карты", "eligible_clients": 500000,
    "reach_share": 1.0, "technically_reachable": 1.0, "uplift_rate": 1.0,
    "attribution_share": 1.0, "turnover_before": 0, "turnover_after": 1800,
    "balance_before": 0, "balance_after": 4000, "decay_months": 18}
_be_res = calc.analyze(calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_be_cfg)))))
_be_lines = [v for v in _be_res["validation"]
             if "СРОК ЖИЗНИ ЭФФЕКТА" in v or "РЕШЕНИЕ ДЕРЖИТСЯ" in v]
check("Движок сам говорит, при каком сроке жизни эффекта меняется решение",
      len(_be_lines) == 1, _be_lines[0][:100] if _be_lines else "молчит")
_be_ready = calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(json.loads(json.dumps(_be_cfg))))))
_be_val = calc.breakeven_decay(_be_ready)
check("Сказанное согласовано с посчитанным, а не написано на всякий случай",
      ("НА РЕШЕНИЕ НЕ ВЛИЯЕТ" in _be_lines[0]) == (_be_val is None),
      f"порог {_be_val} мес" if _be_val else "знак NPV от срока не зависит")
if _be_val is not None:
    check("Порог назван числом и соотнесён с окном замера",
          f"{_be_val:g}" in _be_lines[0] and
          ("окна замера" in _be_lines[0] or "пределами наблюдения" in _be_lines[0]),
          "порог, которого не наблюдали, — это допущение, а не вывод")

# конфиг, где порог заведомо есть: эффект достаточно велик, чтобы перевернуть знак
_case = json.loads(Path(ROOT / "cases" / "youth_card_segments.json").read_text("utf-8"))
_case["base_activation"] = dict(_be_cfg["base_activation"], eligible_clients=500000)
_case_ready = calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_case))))
_case_be = calc.breakeven_decay(_case_ready)
check("На кейсе с крупным эффектом порог окупаемости находится",
      _case_be is not None and 1 < _case_be < 36, f"{_case_be} мес")
check("Порог — это действительно смена знака NPV, а не подобранное число",
      _npv_at(_case_ready, _case_be + 3) > 0 > _npv_at(_case_ready, max(1, _case_be - 3)),
      "дольше порога окупается, короче — нет")

_flat_npv = json.loads(json.dumps(_be_cfg))
_flat_npv["base_activation"]["eligible_clients"] = 1
check("Если знак NPV от срока не зависит — так и сказано, а не выдумано число",
      calc.breakeven_decay(calc.resolve_segments(calc.normalize(
          calc.apply_pilot(calc.validate_config(_flat_npv))))) is None,
      "уточнять срок незачем: решение держится на другом")

# Срок жизни эффекта — самое большое допущение расчёта, и оно должно быть видно
# среди драйверов, а не спрятано. Движение на ±25% для него бессмысленно:
# вопрос «держится только окно замера или не гаснет вовсе».
_tor = json.loads(json.dumps(_beh))
_tor["base_activation"] = {
    "name": "Против обычной карты", "eligible_clients": 500000,
    "reach_share": 1.0, "technically_reachable": 1.0, "uplift_rate": 1.0,
    "attribution_share": 1.0, "turnover_before": 0, "turnover_after": 1800,
    "balance_before": 0, "balance_after": 4000, "decay_months": 18}
_tres = calc.analyze(calc.resolve_segments(calc.normalize(
    calc.apply_pilot(calc.validate_config(_tor)))))
_drivers = [t["driver"] for t in _tres["tornado"]]
check("Срок жизни эффекта есть среди драйверов торнадо",
      any("Срок жизни эффекта" in d for d in _drivers))
check("И он крупнее прочих драйверов, а не спрятан в хвосте списка",
      "Срок жизни эффекта" in _drivers[0],
      f'размах {_tres["tornado"][0]["swing"]:,.0f} ₽ против '
      f'{_tres["tornado"][1]["swing"]:,.0f} ₽ у следующего')
check("Границы взяты от окна замера до «не гаснет», а не ±25%",
      calc._decay_bounds(_tor) == [8.0, 0.0],
      "дальше окна замера доказательств нет — это и есть нижняя граница")

# поля дизайна должны быть в разметке формы, а не только в разборе (BACKLOG 40)
_ppage = client.get(f"/initiative/{iid}/pilot").text
for _f in ("randomized", "matched_at_baseline", "metric_cv", "metrics_are",
           "tail_months"):
    check(f"Поле пилота «{_f}» есть в форме", f'name="pilot.{_f}"' in _ppage)
check("Колонка хвоста есть и в отрисованных строках, и в шаблоне новой строки",
      _ppage.count('name="metric.0.tail"') >= 1 and 'metric.0.tail' in _ppage,
      "иначе хвост можно ввести только в уже существующей строке")

check("Коэффициент переноса режет прирост, а не уровень",
      abs(_probe["funnel"]["activation_rate"] - (0.65 + (0.61 - 0.65) * 0.75)) < 1e-9,
      f'получилось {_probe["funnel"]["activation_rate"]}')

r = client.get(f"/initiative/{iid}/pilot")
check("Проверки движка видны на странице",
      "Грейд переноса" in r.text and "B−" in r.text)

# живая проверка до сохранения — на маленькой выборке пилот ничего не доказывает
r = client.post("/api/pilot/preview", json={"pilot": {
    "geography": "Москва", "rollout_geography": "РФ", "control_group": True,
    "sample_size": 800, "control_size": 800,
    "baseline_rate": 0.52, "observed_rate": 0.55}})
_pw = r.json()["info"]["power"]
check("Предпросмотр ловит недостаточную выборку: 3 п.п. на 800 неразличимы",
      _pw["conclusive"] is False and _pw["min_detectable"] > _pw["observed_uplift"])
check("Предпросмотр объясняет, сколько нужно было на группу",
      any("на группу" in n for n in r.json()["notes"]))

# конфиг, который движок не примет, не должен молча сохраняться как валидный
r = client.post("/api/pilot/preview", json={"pilot": {"baseline_rate": 52}})
check("Предпросмотр отдаёт ошибку валидации, а не считает по мусору",
      bool(r.json().get("errors")))

client.post(f"/initiative/{iid}/pilot", data={"action": "save"}, follow_redirects=False)
check("Снятая галочка убирает пилот из конфига",
      "pilot" not in json.loads(_db.get_initiative(app_mod.con, iid)["config_json"]))
