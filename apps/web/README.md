# web scaffold

Next.js ダッシュボード用の叩き台です。

## Local development

Node.js 20+ と npm を利用します。

```bash
npm ci
npm run dev
```

ローカルで依存更新する場合:

```bash
npm install
```

API の接続先は環境変数で指定できます。

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Codex 環境では npm install が失敗する場合があります。ローカルでは package-lock.json に従ってインストールできます。
