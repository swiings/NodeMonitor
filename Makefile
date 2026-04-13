.PHONY: install run-display run-listener build clean

VENV := $(HOME)/.venv-nodemonitor
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

# Create venv and install all Python dependencies
install:
	python3 -m venv $(VENV)
	$(PIP) install -r NMDisplay/requirements.txt
	$(PIP) install -r NMListener/requirements.txt

# Run NMDisplay (the web UI + listener manager)
run-display:
	cd NMDisplay && $(PYTHON) app.py

# Run NMListener standalone (without NMDisplay)
run-listener:
	cd NMListener && $(PYTHON) main.py

# Build the macOS .app bundle with PyInstaller
build:
	$(PIP) install pyinstaller
	$(PIP) install -r NMDisplay/requirements.txt -r NMListener/requirements.txt
	$(VENV)/bin/pyinstaller NodeMonitor.spec

clean:
	rm -rf build dist
