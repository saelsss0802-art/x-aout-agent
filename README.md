# x-aout-agent

x-aout-agent の monorepo 初期骨格です。`docker compose` で Postgres + API を起動し、worker はローカル実行できます。

## ディレクトリ構成

- `apps/api`: FastAPI (Supervisor/API)
- `apps/web`: Next.js Dashboard の叩き台
- `apps/worker`: APScheduler ベースの worker
- `packages/core`: 共有コード（DB base/models/interfaces）
- `docs`: 設計・仕様ドキュメント
- `infra/docker-compose.yml`: ローカル開発用 compose
- `scripts/dev.sh`: API + worker をローカルで同時起動
- `scripts/seed.py`: ダミー seed スクリプト

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 起動方法

### 1) Postgres + API を Docker で起動

```bash
docker compose -f infra/docker-compose.yml up --build
```

API ヘルスチェック:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 2) worker をローカルで 1 回実行

```bash
python -m apps.worker.run_once
```

### 3) API + worker をローカルで同時起動

```bash
./scripts/dev.sh
```

## マイグレーション（Alembic）

```bash
alembic -c apps/api/alembic.ini upgrade head
```

## Seed（ダミーデータ）

```bash
python scripts/seed.py
```

## テスト

```bash
pytest
```
