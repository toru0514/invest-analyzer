# 株価シグナル通知アプリ 開発用 Makefile
#
#   make setup   ... backend venv 構築 + frontend npm install
#   make backend ... Python API (FastAPI) を :8000 で起動
#   make frontend... Next.js を :3000 で起動
#   make dev     ... 両方を一括起動（Ctrl-C で両方停止）
#   make phase0  ... Phase 0 バックテストをコンソール実行
#   make test    ... backend のスモークテスト

PY := backend/venv/bin/python
PIP := backend/venv/bin/pip
UVICORN := backend/venv/bin/uvicorn

.PHONY: setup backend frontend dev phase0 test clean

setup:
	python3.12 -m venv backend/venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
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
	cd backend && venv/bin/python test_signals.py

clean:
	rm -f data.db data.db-wal data.db-shm
