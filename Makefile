CASE ?= cases/frk1_debit_card.json
XLSX  = docs/Калькулятор_эффекта_инициатив.xlsx
BUILD = .build
VENV  = .venv
PORT ?= 8077
# venv нужен только веб-части; движок и книга работают на системном python
PY    = $(shell [ -x $(VENV)/bin/python ] && echo $(VENV)/bin/python || echo python3)

.PHONY: help calc cases test test-web test-all xlsx check check-all sync web venv clean

help:
	@echo "make web                          — запустить веб-приложение (порт $(PORT))"
	@echo "make venv                         — поставить зависимости веб-части в .venv"
	@echo "make calc CASE=cases/<файл>.json  — расчёт одного кейса"
	@echo "make test                         — стресс-тесты движка"
	@echo "make test-web                     — тесты веб-пути"
	@echo "make test-all                     — движок + веб + сверка со всеми книгами"
	@echo "make xlsx                         — пересобрать Excel-модель"
	@echo "make check                        — тесты + сверка Excel с движком по CASE"
	@echo "make check-all                    — сверка Excel с движком по всем кейсам"
	@echo "make sync                         — синхронизировать копию движка в навыке"
	@echo "make cases                        — прогнать все кейсы разом"
	@echo "make backup [KEEP=20]             — снимок базы расчётов в .data/backups/"

calc:
	@$(PY) src/calc.py $(CASE)

cases:
	@for f in cases/*.json; do \
		echo "=== $$f ==="; \
		$(PY) src/calc.py $$f | $(PY) -c "import json,sys; d=json.load(sys.stdin); \
p=d['pl']; f=d['funnel']; \
print(f\"  инкремент {f['incremental_clients']:,}  NPV {p['npv']:,}  окупаемость {p['payback_months']}\"); \
print(f\"  {d['verdict']['recommendation']}\")"; \
	done

test:
	@$(PY) tests/stress_test.py

xlsx:
	@$(PY) src/build_xlsx.py $(CASE) && echo "Excel собран: $(XLSX)"

sync:
	@cp src/calc.py src/params.py .claude/skills/initiative-impact-calculator/scripts/
	@cp cases/*.json .claude/skills/initiative-impact-calculator/reference/
	@echo "навык синхронизирован с src/"

check: test
	@$(PY) tests/recalc.py "$(XLSX)" "$(BUILD)/recalc.xlsx"
	@$(PY) tests/check_xlsx_sync.py $(CASE) "$(BUILD)/recalc.xlsx"

# Excel собирается под конкретный кейс, поэтому сверять надо каждый: одни ловят лестницу ФРК,
# другие — выплаты за привлечение и инверсию сценариев.
check-all: test
	@for f in cases/*.json; do \
		echo ""; echo "=== $$f ==="; \
		$(PY) src/build_xlsx.py $$f "$(BUILD)/model.xlsx" >/dev/null && \
		$(PY) tests/recalc.py "$(BUILD)/model.xlsx" "$(BUILD)/recalc.xlsx" >/dev/null && \
		$(PY) tests/check_xlsx_sync.py $$f "$(BUILD)/recalc.xlsx" || exit 1; \
	done

venv:
	@python3 -m venv $(VENV)
	@$(VENV)/bin/pip -q install -r requirements.txt && echo "зависимости установлены в $(VENV)"

web: $(VENV)/bin/uvicorn
	@echo "Открой http://127.0.0.1:$(PORT)"
	@$(VENV)/bin/uvicorn web.app:app --host 127.0.0.1 --port $(PORT) --reload \
		--reload-dir web --reload-dir src

$(VENV)/bin/uvicorn:
	@$(MAKE) venv

test-web: $(VENV)/bin/uvicorn
	@$(VENV)/bin/python tests/test_web.py

test-all: check-all test-web

# Снимок базы. Через Connection.backup(), а не cp: база в режиме WAL, и копия файла
# унесла бы снимок без последних транзакций — то есть без самых свежих расчётов.
KEEP ?= 20
backup:
	@$(PY) tests/backup.py $(KEEP)

# .data здесь НЕТ и быть не должно: это рабочие расчёты, а не мусор сборки.
clean:
	@rm -rf __pycache__ src/__pycache__ tests/__pycache__ web/__pycache__ .pytest_cache $(BUILD)
