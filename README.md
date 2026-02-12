# x-aout-agent

x-aout-agent の monorepo 初期骨格です。**永続DBは Supabase（ホストPostgres）を唯一の前提**とし、ローカル開発で Docker 必須にしない構成にしています。

## ディレクトリ構成

- `apps/api`: FastAPI (Supervisor/API)
- `apps/web`: Next.js Dashboard の叩き台
- `apps/worker`: APScheduler ベースの worker
- `packages/core`: 共有コード（DB base/models/interfaces）
- `docs`: 設計・仕様ドキュメント
- `infra/docker-compose.yml`: legacy（任意利用。標準手順では非推奨）
- `scripts/dev.sh`: API + worker をローカルで同時起動
- `scripts/seed.py`: ダミー seed スクリプト

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e packages/core
cp .env.example .env
```

`.env` は Supabase 前提で設定します。

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`
- `SUPABASE_SERVICE_ROLE_KEY`（**バックエンド専用。フロントエンドへ絶対に露出しないこと**）
- `DATABASE_URL`（Supabase Postgres 接続文字列。Alembic / SQLAlchemy が利用）
  - 推奨形式: `postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME`

### DATABASE_URL の取得方法（Supabase Dashboard）

1. Supabase Dashboard で対象プロジェクトを開く
2. **Connect** ボタンを押す
3. Connection string をコピーして `.env` の `DATABASE_URL` に設定する
4. SQLAlchemy で動く形（`postgresql+psycopg://...`）になっていることを確認する

> 補足: ローカル回線やISP都合で IPv6 が不安定な場合は、Connect タブで **pooler（IPv4互換）** の接続先を使ってください。


### DATABASE_URL の動作例（SQLAlchemy）

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME
```

Supabase の pooler を使う場合も同じ形式で、HOST/PORT を Connect タブで示される pooler の値に置き換えてください。

## 起動方法（Docker なし）

```bash
./scripts/dev.sh
```

`dev.sh` は以下を順に実行します。

1. venv が有効かチェック
2. 必須モジュール（`alembic`, `sqlalchemy`, `psycopg`, `uvicorn`, `core`）の import 可否をチェック
3. `DATABASE_URL` の設定をチェック
4. `python -m alembic -c apps/api/alembic.ini upgrade head`
5. `uvicorn` 起動 + worker 起動

依存が不足している場合は、セットアップ手順（`pip install -r requirements.txt` / `pip install -e packages/core`）を案内して停止します。

API ヘルスチェック:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## マイグレーション（Alembic）

```bash
python -m alembic -c apps/api/alembic.ini upgrade head
```

`apps/api/alembic/env.py` は `core.db.Base.metadata` を `target_metadata` として参照します。モデル定義は `packages/core` 側へ集約する方針です。

## Seed（ダミーデータ）

```bash
python scripts/seed.py
```

## テスト

```bash
pytest
```

## Legacy: Docker compose

`infra/docker-compose.yml` は後方互換のため残していますが、標準のローカル開発手順では使用しません。
