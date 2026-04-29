# Docs

Kenny Bot のドキュメントは `docs/` 配下に集約します。

## 構成

- `architecture/`: 設計、フロー、応答アーキテクチャなどの実装者向け文書
- `development/`: ローカル preview、tool API などの開発補助文書

## 主な文書

- [system-design.md](/home/kennypi/work/Kenny-bot/docs/architecture/system-design.md): 全体設計と責務分割
- [message-flow.md](/home/kennypi/work/Kenny-bot/docs/architecture/message-flow.md): メッセージ処理フロー
- [response-architecture.md](/home/kennypi/work/Kenny-bot/docs/architecture/response-architecture.md): 応答品質と source priority の規範
- [bot-talk.md](/home/kennypi/work/Kenny-bot/docs/architecture/bot-talk.md): 旧会話仕様メモ
- [local-profile-preview.md](/home/kennypi/work/Kenny-bot/docs/development/local-profile-preview.md): profile preview のローカル検証
- [local-tool-api.md](/home/kennypi/work/Kenny-bot/docs/development/local-tool-api.md): tool API のローカル起動と仕様

## 運用ルール

- 新しい設計判断は `docs/architecture/` に追加する
- 開発用の手順書や preview 系のメモは `docs/development/` に追加する
- `README.md` は利用者向け概要に集中させる
