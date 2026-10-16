# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## コマンド

すべて `uv run` 経由で実行する（venv の activate や pip は使わない）。依存追加は `uv add` / `uv add --dev`。

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

ruff の行長は 120。

## テスト

- `config.json` を読み書きするテストは必ず `config_path` fixture（`tests/conftest.py`）を使う。使わないとリポジトリ直下の実際の `config.json` を書き換えてしまう。
- pytest-asyncio は入れていない。async 関数のテストは既存テストと同じく `asyncio.run(...)` で実行する。

## 注意点

- ボタンの `custom_id`（`wasabi:eat` など、レア版は末尾に `:rare`）は変更しない。`setup_hook` で永続 View として登録しており、変えると投稿済みのボタンが Bot 再起動後に反応しなくなる。
- ユーザー向けの文言や `KEYWORD_REPLIES` は `src/wasabi_bot/replies.py` に集約されている。

## 運用

- コマンド・キーワード・文言・挙動を変えたら、`README.md` の該当箇所（主に「機能」節）も同じコミットで更新する。
- master に直接コミットする。コミットメッセージは日本語で書く。
