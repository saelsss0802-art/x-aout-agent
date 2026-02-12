# API Budget

## 予算ガード方式

- 入力: `max_tokens`, `max_runtime_sec`, `daily_budget`
- API は run 作成時に `max_tokens <= tier_limit` を検証
- Worker は実行中に消費量を記録し、しきい値で停止

## 判定フロー

1. API 受付時に静的上限チェック
2. 実行前に日次残量チェック
3. 実行中にリアルタイム監視
4. 超過時は `stopped_budget_exceeded` で終了
