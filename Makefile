-include .env
export

.PHONY: setup awake run clean say migrate test dashboard errand-run errand-awake install sync-instance docker-setup docker

VENV := .venv
PYTHON := $(VENV)/bin/python3

setup: $(VENV)/.installed

$(VENV)/.installed: koan/requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r koan/requirements.txt
	@touch $@

awake: setup
	cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/awake.py

run:
	./koan/run.sh

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

errand-run:
	caffeinate -i ./koan/run.sh

errand-awake: setup
	caffeinate -i sh -c 'cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. ../$(PYTHON) app/awake.py'

install:
	@echo "→ Starting Kōan Setup Wizard..."
	@python3 -m venv $(VENV) 2>/dev/null || true
	@$(VENV)/bin/pip install -q flask 2>/dev/null || pip3 install -q flask 2>/dev/null
	@cd koan && KOAN_ROOT=$(PWD) PYTHONPATH=. python3 app/setup_wizard.py

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

docker-setup:
	@./setup-docker.sh

docker: docker-setup
	docker compose up --build
