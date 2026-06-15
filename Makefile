# 株価シグナル通知アプリ 開発用 Makefile
#
#   make setup   ... backend venv 構築 + frontend npm install
#   make backend ... Python API (FastAPI) を :8000 で起動
#   make frontend... Next.js を :3000 で起動
#   make dev     ... 両方を一括起動（Ctrl-C で両方停止）
#   make phase0  ... Phase 0 バックテストをコンソール実行
#   make test    ... backend テスト一式（pytest: 計算コア / API結合 / スケジューラ）

PY := backend/venv/bin/python
PIP := backend/venv/bin/pip
UVICORN := backend/venv/bin/uvicorn

# venv 作成に使う Python（3.12 以上が必要）。3.12 が無ければ 3.13 / python3 に
# フォールバック。明示する場合は `make setup PYTHON=python3.12` のように上書き可。
PYTHON ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3)

.PHONY: setup backend frontend dev phase0 test clean

setup:
	$(PYTHON) -m venv backend/venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
	$(PIP) install -r backend/requirements-dev.txt
	cd frontend && npm install

backend:
	cd backend && venv/bin/uvicorn main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

# 両プロセスを一括起動（どちらかが落ちたら両方停止）
dev:
	@echo "API(:8000) と Next.js(:3000) を起動します。Ctrl-C で停止。"
	@trap 'kill 0' INT TERM EXIT; \
	(cd backend && venv/bin/uvicorn main:app --reload --port 8000) & \
	(cd frontend && npm run dev) & \
	wait

phase0:
	cd backend && venv/bin/python phase0_backtest.py $(ARGS)

test:
	cd backend && venv/bin/python -m pytest test_signals.py test_api.py test_scheduler.py -q

clean:
	rm -f data.db data.db-wal data.db-shm
