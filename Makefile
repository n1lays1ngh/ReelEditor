# PaintReel — run `make install` once, then `make dev`.
PYTHON ?= python3.13
VENV := backend/.venv
PY := $(VENV)/bin/python
# PYTHONPATH=src: editable-install .pth files can get the macOS "hidden" flag (e.g. iCloud Desktop), which Python skips.
export PYTHONPATH := $(CURDIR)/backend/src

.PHONY: install dev api ui test check-ffmpeg clean-cache

install: check-ffmpeg
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e "backend[dev]"
	cd frontend && npm install

dev:
	./scripts/dev.sh

api:
	cd backend && ../$(VENV)/bin/uvicorn paintreel.api.app:create_app --factory --reload --reload-dir src --port 8000

ui:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest -q
	cd frontend && npx tsc -b

check-ffmpeg:
	@command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || \
		(echo "ffmpeg/ffprobe not found. Install with: brew install ffmpeg"; exit 1)

clean-cache:
	rm -rf backend/data/cache
