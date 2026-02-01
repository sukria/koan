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
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/awake.py

run:
	./koan/run.sh

say:
	@test -n "$(m)" || (echo "Usage: make say m=\"your message\"" && exit 1)
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. $(PYTHON) -c "from app.awake import handle_message; handle_message('$(m)')"

test: setup
	$(VENV)/bin/pip install -q pytest 2>/dev/null
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m pytest tests/ -v

migrate: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/migrate_memory.py

dashboard: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/dashboard.py

clean:
	rm -rf $(VENV)
