-include .env
export

.PHONY: setup awake run clean say migrate test dashboard

VENV := .venv
PYTHON := $(VENV)/bin/python3

setup: $(VENV)/bin/activate

$(VENV)/bin/activate: koan/requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r koan/requirements.txt
	@touch $(VENV)/bin/activate

awake: setup
	$(PYTHON) koan/awake.py

run:
	./koan/run.sh

say:
	@test -n "$(m)" || (echo "Usage: make say m=\"your message\"" && exit 1)
	@cd koan && $(PYTHON) -c "from awake import handle_message; handle_message('$(m)')"

test: setup
	$(VENV)/bin/pip install -q pytest 2>/dev/null
	cd koan && ../$(PYTHON) -m pytest -v

migrate: setup
	$(PYTHON) koan/migrate_memory.py

dashboard: setup
	$(PYTHON) koan/dashboard.py

clean:
	rm -rf $(VENV)
