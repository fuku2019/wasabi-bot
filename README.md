# わさび Discord Bot

指定した時刻に、設定したDiscordチャンネルへ「わさび」と自動投稿するBotです。投稿にはボタンが付き、押すたびに次のメッセージとボタンへ進みます。

## 機能

- `/set_channel`
  - コマンドを実行したチャンネルを、自動投稿先として `config.json` に保存します。
- `/set_time <HH:MM>`
  - 自動投稿時刻を24時間表記で `config.json` に保存します。
  - 例: `/set_time 09:30`
- `/わさび`
  - 「くっだらねえ！なんだよそれ！わっかわっかしい！」と返します。
- キーワード自動応答（メッセージの先頭が一致したときだけ反応します）
  - `わさび…` → 「くっだらねえ！なんだよそれ！わっかわっかしい！」
  - `わらび餅…` → 「わさび餅」
  - Bot自身を含め、Botの発言には反応しません。
- 指定時刻になると、自動投稿先に「わさび」と投稿します。
- ボタンの進行フロー
  - `[食べる]` → ボタン削除 → 「鼻がツーンとした！」 + `[ティッシュにぺ！ってする]`
  - `[ティッシュにぺ！ってする]` → ボタン削除 → 「お前は許されないことした！」 + `[下ネタを聞く]`
  - `[下ネタを聞く]` → ボタン削除 → 「すまん！悪かった！」

Botトークンは `.env` から読み込みます。自動投稿先や投稿時刻は `config.json` に保存され、Botを再起動しても引き継がれます。

## 事前準備

Discord Developer PortalでBotを作成し、Botトークンを取得してください。Botをサーバーへ招待するときは、以下の権限を付与してください。

- `Send Messages`
- `Use Slash Commands`
- `Read Message History`

スラッシュコマンドの設定操作は、サーバー管理権限を持つユーザーが実行できます。

### Message Content Intent の有効化

キーワード自動応答にはメッセージ本文の読み取り権限が必要です。Discord Developer Portal で対象Botの `Bot` タブを開き、`Privileged Gateway Intents` の **MESSAGE CONTENT INTENT** をONにしてください。

ONにしていない場合、Botは起動しますがメッセージ本文が空で届くため、キーワード自動応答だけが無反応になります（スラッシュコマンドと自動投稿は動きます）。

## 環境変数

`.env.example` を参考に `.env` を作成します。

```env
DISCORD_TOKEN=your_discord_bot_token_here
```

開発中にスラッシュコマンドをすぐ反映したい場合は、対象サーバーのIDを追加できます。

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=123456789012345678
```

`DISCORD_GUILD_ID` を設定しない場合、スラッシュコマンドはグローバル同期され、Discord側への反映に時間がかかることがあります。

## セットアップと実行

[uv](https://docs.astral.sh/uv/) だけあれば動きます。仮想環境の作成・依存インストール・実行は `uv run` が全部やるので、`activate` も `pip install` も不要です。

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
notepad .env
uv run bot.py
```

macOS / Linux:

```bash
cp .env.example .env
nano .env
uv run bot.py
```

2回目以降は `uv run bot.py` だけです。Pythonのバージョンは `.python-version`、依存のバージョンは `uv.lock` で固定されているので、別のマシンでも同じ環境が再現されます。

## 開発

```bash
uv run pytest          # テスト
uv run ruff check .    # lint
uv run ruff format .   # フォーマット
```

依存を追加するときは `uv add パッケージ名`、開発用ツールなら `uv add --dev パッケージ名` です。どちらも `pyproject.toml` と `uv.lock` が自動更新されます。

## 常用・本番での起動

開発ツールを入れずに、`uv.lock` のとおりの依存だけで動かす場合:

```bash
uv sync --frozen --no-dev
uv run --no-dev bot.py
```

`--frozen` はロックファイルを更新せずにそのまま使う指定です。開発環境に戻すときは `uv sync` を実行してください。

## Botの使い方

1. Botを起動します。
2. 自動投稿したいチャンネルで `/set_channel` を実行します。
3. `/set_time 09:30` のように投稿時刻を設定します。
4. 指定時刻になると、設定したチャンネルに「わさび」が投稿されます。

投稿時刻はBotを動かしている環境のローカル時刻で判定されます。

## ファイル構成

```text
.
├── bot.py
├── test_bot.py
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── .gitignore
└── README.md
```

`config.json` はBot実行時に自動作成されます。`.env` と `config.json` は `.gitignore` に含まれているため、Gitにはコミットされません。`uv.lock` は依存バージョンを固定するためのファイルなので、Gitにコミットします。
