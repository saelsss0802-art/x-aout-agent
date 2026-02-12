# UI Spec

## 画面一覧

- `/` ダッシュボード概要
- `/runs` 実行履歴一覧
- `/runs/[id]` 実行詳細
- `/policy` ポリシー状態・停止理由
- `/settings/budget` 予算設定

## ルーティング叩き台

- Next.js App Router を採用予定
- API 接続先は `/api/*` を BFF 経由で統一予定
- MVP では read-only ダッシュボードを優先
