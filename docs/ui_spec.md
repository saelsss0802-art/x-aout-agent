# UI仕様（ルーティングと主要コンポーネント）

## 画面一覧
- /dashboard
  - 全アカサマリー、当日予算消費、直近7日グラフ、アラート
- /accounts
  - アカ一覧（状態、当日予算、週次focus、次回実行、緊急停止）
- /accounts/[id]
  - トグル、予算、ターゲットアカ設定、素材フォルダ、週次focus、最新PDCAログ
- /onboarding/new
  - 会話形式でアカ固有ナレッジ生成（理想アカURL入力含む）
- /schedule
  - 予約投稿一覧、カレンダー、手動追加/編集
- /experiments
  - 実験一覧、作成、variant紐付け、結果
- /knowledge/shared
  - 共有ナレッジ一覧、フィルタ、詳細（仮説/検証/結果/適用条件）
- /logs/cost
  - CostLog一覧、日次推移、内訳（X/LLM）
- /safety
  - 緊急停止（全体/個別）、停止理由、復帰操作

## 主要UIコンポーネント
- AccountCard
- BudgetBar（X/LLM別）
- WeeklyFocusChip
- PDCAViewer（JSON pretty + human summary）
- ExperimentTable
- KnowledgeDetail
- EmergencyStopModal
