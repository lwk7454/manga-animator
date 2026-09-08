# manga-animator — task runner (vidgen pattern)
# Persistent venv lives at ~/.local/share/koma-venv (editable install).

VENV := $(HOME)/.local/share/koma-venv
PY   := $(VENV)/bin/python
UV   := uv

.PHONY: venv install test lint health clean

## Create the persistent venv (run once)
venv:
	$(UV) venv --python 3.11 $(VENV)

## Editable install + global launcher (source edits take effect, no reinstall)
install:
	$(UV) pip install --python $(VENV)/bin/python -e ".[dev]"
	@mkdir -p $(HOME)/.local/bin
	@cp $(VENV)/bin/koma $(HOME)/.local/bin/koma
	@echo "✅ koma installed → $(HOME)/.local/bin/koma"

## Run the test suite
test:
	$(PY) -m pytest tests/ -q

## Lint
lint:
	$(VENV)/bin/ruff check src tests

## Environment check
health:
	$(PY) -m koma.cli health

## Remove build artifacts (venv is persistent, keep it)
clean:
	rm -rf src/*.egg-info build dist
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true