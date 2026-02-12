-include .env
export

.PHONY: install setup start stop status
.PHONY: clean say migrate test sync-instance
.PHONY: awake run errand-run errand-awake dashboard
.PHONY: ollama logs

PYTHON_BIN ?= python3

VENV   ?= .venv
PYTHON ?= $(VENV)/bin/$(PYTHON_BIN)

setup: $(VENV)/.installed

$(VENV)/.installed: koan/requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r koan/requirements.txt
	@touch $@

awake: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/awake.py

run: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/run.py

say: setup
	@test -n "$(m)" || (echo "Usage: make say m=\"your message\"" && exit 1)
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -c "from app.awake import handle_message; handle_message('$(m)')"

test: setup
	$(VENV)/bin/pip install -q pytest 2>/dev/null
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m pytest tests/ -v

migrate: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/migrate_memory.py

dashboard: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/dashboard.py

start: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager start-all $(PWD)

stop: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager stop-all $(PWD)

status: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager status-all $(PWD)

errand-run: setup
	caffeinate -i $(MAKE) run

errand-awake: setup
	caffeinate -i sh -c 'cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/awake.py'

ollama: setup
	@echo "→ Starting Kōan with Ollama stack..."
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager start-stack $(PWD)

logs:
	@mkdir -p logs
	@if [ ! -f logs/run.log ] && [ ! -f logs/awake.log ] && [ ! -f logs/ollama.log ]; then \
		echo "No log files found. Start Kōan first with 'make start'."; \
		exit 1; \
	fi
	@echo "→ Watching Kōan logs (Ctrl-C to stop watching — Kōan keeps running)"
	@tail -F logs/run.log logs/awake.log logs/ollama.log 2>/dev/null

install:
	@echo "→ Starting Kōan Setup Wizard..."
	@$(PYTHON) -m venv $(VENV) 2>/dev/null || true
	@$(VENV)/bin/pip install -q flask 2>/dev/null || pip3 install -q flask 2>/dev/null
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. $(PYTHON) app/setup_wizard.py

clean:
	rm -rf $(VENV)

sync-instance:
	@mkdir -p instance
	@for f in instance.example/*; do \
		name=$$(basename "$$f"); \
		if [ ! -e "instance/$$name" ]; then \
			echo "→ Copying $$name"; \
			cp -r "$$f" "instance/$$name"; \
		fi; \
	done
	@echo "✓ instance/ synced with instance.example/"
