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

## 標準セットアップ（Supabase + Docker なし）

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
  - **必須**: 未設定時はアプリ/マイグレーションは起動せず `RuntimeError` で停止します（localhostへのフォールバックはしません）。

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


## X API v2 integration

Set `USE_REAL_X=1` to use X API v2 instead of `FakeXClient` in the worker routine.

Required and recommended variables:

- `X_BEARER_TOKEN` (required when `USE_REAL_X=1`)
- `X_USER_ID` (recommended; required when `/2/users/me` cannot be used in your auth context)
- `USE_REAL_X=1`

Usage API (`GET /2/usage/tweets`) can be enabled with:

- `USE_X_USAGE=1`
- `X_UNIT_PRICE` (optional per-unit price for local cost conversion)

X API v2 is pay-per-usage and endpoint unit prices are configured in the Developer Console. This project stores usage units (`x_usage_units`) and raw usage payload (`x_usage_raw`) in `CostLog`, then optionally converts units to `x_api_cost` via `X_UNIT_PRICE`.

X API related worker tests use httpx mocks and require the httpx package to be installed in the test environment.


OAuth user context (PKCE) for posting requires scopes mapped to X API v2 endpoints.
Use tweet.write for POST /2/tweets, users.read for GET /2/users/me, and offline.access for refresh tokens.
Optionally include tweet.read when fetching tweet details/metrics in user context flows.


## Web (apps/web) セットアップと起動

Node.js 20 以上を推奨します。

```bash
cd apps/web
npm ci
# またはローカル開発では npm install
npm run dev
```

API のベース URL を変更したい場合は環境変数で指定できます。

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Codex 環境では npm install がセキュリティ制限で失敗する場合がありますが、ローカルでは lockfile に従って起動できます。

## 起動手順（Docker なし / 標準）

標準の起動順は次のとおりです。

1. Python venv を作成して有効化
2. 依存をインストール
3. `.env` を作成し、Supabase の値を設定
4. Alembic でマイグレーション
5. API / worker を起動

### 1〜4 を一括で実施

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e packages/core
cp .env.example .env
python -m alembic -c apps/api/alembic.ini upgrade head
```

### 5. API / worker の起動

別々のターミナルで起動する場合:

```bash
# terminal 1
source .venv/bin/activate
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload

# terminal 2
source .venv/bin/activate
python -m apps.worker.main
```

同時起動する場合（補助スクリプト）:

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

※ `dev.sh` 自体は `pip install` を実行しません。依存解決はセットアップ時に一度だけ行います。

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
pytest -q
```

## Legacy / Optional: Docker compose

`infra/docker-compose.yml` は後方互換のため残していますが、標準のローカル開発手順では使用しません。
