-include .env
export

.PHONY: install setup start stop status
.PHONY: clean say migrate test sync-instance
.PHONY: awake run errand-run errand-awake dashboard
.PHONY: ollama logs
.PHONY: install-systemctl-service uninstall-systemctl-service
.PHONY: docker-setup docker-up docker-down docker-logs docker-test

PYTHON_BIN ?= python3

VENV   ?= .venv
PYTHON ?= $(VENV)/bin/$(PYTHON_BIN)

# --- systemd detection (Linux only, never on macOS) ---
IS_LINUX := $(shell [ "$$(uname -s)" = "Linux" ] && echo 1)
HAS_SYSTEMD := $(if $(IS_LINUX),$(shell command -v systemctl >/dev/null 2>&1 && echo 1))
USE_SYSTEMD := $(if $(HAS_SYSTEMD),1)
SERVICE_INSTALLED = $(shell [ -f /etc/systemd/system/koan.service ] && echo 1)

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

ifeq ($(USE_SYSTEMD),1)

start: setup
	@if [ -z "$(SERVICE_INSTALLED)" ]; then \
		echo "→ systemd detected — installing Kōan service (one-time setup)..."; \
		sudo CALLER_PATH="$$PATH" bash koan/systemd/install-service.sh "$(PWD)" "$(PWD)/$(PYTHON)"; \
	fi
	@sudo systemctl start koan

stop:
	@sudo systemctl stop koan koan-awake

status:
	@sudo systemctl status koan koan-awake --no-pager || true

else

start: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager start-all $(PWD)

stop: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager stop-all $(PWD)

status: setup
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) -m app.pid_manager status-all $(PWD)

endif

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

install-systemctl-service: setup
	@if [ -z "$(IS_LINUX)" ]; then echo "Error: systemd is only available on Linux." >&2; exit 1; fi
	@if [ -z "$(HAS_SYSTEMD)" ]; then echo "Error: systemctl not found. systemd is required." >&2; exit 1; fi
	sudo CALLER_PATH="$$PATH" bash koan/systemd/install-service.sh "$(PWD)" "$(PWD)/$(PYTHON)"

uninstall-systemctl-service:
	@-$(MAKE) stop
	@if [ -z "$(IS_LINUX)" ]; then echo "Error: systemd is only available on Linux." >&2; exit 1; fi
	@if [ -z "$(HAS_SYSTEMD)" ]; then echo "Error: systemctl not found." >&2; exit 1; fi
	sudo bash koan/systemd/uninstall-service.sh

# --- Docker targets ---

docker-setup:
	@./setup-docker.sh

docker-up: docker-setup
	docker compose up --build -d
	@echo "→ Kōan running in Docker. Use 'make docker-logs' to watch output."

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

docker-test:
	docker compose run --rm koan test
