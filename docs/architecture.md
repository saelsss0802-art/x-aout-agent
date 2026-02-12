# アーキテクチャ詳細

## 実行モデル
- Worker は「日次ジョブ」をアカウント単位でキュー実行する（逐次 or 小規模並列）
- すべての外部アクセス（X API / Web検索 / LLM）はインターフェース越しに呼び出し、差し替え可能にする

## 主要インターフェース（core）
- XClient
  - list_posts(date_range, account)
  - get_post_metrics(post_ids, kind=confirmed|snapshot)
  - create_post / schedule_post
  - like / reply / quote
- SearchClient
  - search_web(query) -> results
  - fetch_readable(url) -> article text
  - search_x(query) -> posts
- LLMClient
  - run(task_type, model_selector, input, constraints) -> output

## 予算ガード（BudgetGuard）
- 行動前に必ず `estimate_cost(action)` を計算
- `remaining_x` / `remaining_llm` を下回る行動は実行禁止
- 日次の残高は DB に保存（CostLog）し、Worker はそこを参照して制御

## Safety Gate（暴走停止）
- 予算超過見込み
- リプ/引用 上限到達
- 短時間の予約集中（スパム挙動）
- 類似投稿の連投（テキスト類似度しきい値）
- ネガ反応急増（指標しきい値）

## 週次 focus
- 週の開始に weekly_focus_kpi を選ぶ
- 1週間はその focus を中心に仮説と実験を組む
- 毎日ログに「focusを選んだ理由」と「今日の行動がfocusにどう寄与するか」を残す
