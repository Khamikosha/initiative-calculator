CASE ?= cases/frk1_debit_card.json
XLSX  = docs/Калькулятор_эффекта_инициатив.xlsx
PY    = python3

.PHONY: help calc cases test xlsx check sync clean

help:
	@echo "make calc CASE=cases/<файл>.json  — расчёт одного кейса"
	@echo "make test                         — стресс-тесты движка"
	@echo "make xlsx                         — пересобрать Excel-модель"
	@echo "make check                        — тесты + сверка Excel с движком"
	@echo "make sync                         — синхронизировать копию движка в навыке"
	@echo "make cases                        — прогнать все кейсы разом"

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
	@$(PY) src/build_xlsx.py && echo "Excel собран: $(XLSX)"

sync:
	@cp src/calc.py .claude/skills/initiative-impact-calculator/scripts/calc.py
	@cp cases/*.json .claude/skills/initiative-impact-calculator/reference/
	@echo "навык синхронизирован с src/"

check: test
	@$(PY) tests/check_xlsx_sync.py

clean:
	@rm -rf __pycache__ src/__pycache__ tests/__pycache__ .pytest_cache
