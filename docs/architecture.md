# Architecture

## 主要インターフェース

- `GET /health`: 稼働確認
- 将来追加: `POST /runs`, `GET /runs/{id}`, `POST /policy/evaluate`

## 実行モデル

1. API がリクエストを受ける
2. DB にジョブ/状態を保存
3. Worker が定期ポーリングして処理
4. 成果物とメタデータを DB に保存

## 予算ガード / 安全装置

- API層: 実行要求時に予算上限を検証
- Worker層: 実行中にトークン/時間の閾値を監視
- Policy Gate: 停止条件に該当した場合は処理を中断
