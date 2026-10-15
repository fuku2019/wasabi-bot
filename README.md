# わさびbot

身内鯖で楽しむために作成したBot。
1日に複数回、指定した時刻に設定したDiscordチャンネルへ「わさび」と自動投稿するBotです。投稿にはボタンが付き、押すたびに次のメッセージとボタンへ進みます。

## 機能

- `/set_channel`（要サーバー管理権限）
  - コマンドを実行したチャンネルを、そのサーバーの自動投稿先として `config.json` に保存します。
- `/set_times`（要サーバー管理権限）
  - そのサーバーの投稿時刻を設定します。カンマ区切りで指定します。例: `/set_times times:00:00, 09:30, 18:00`
  - 最大24件まで設定できます。`9:30` のように時を1桁で書いても受け付けます。
  - 今日すでに過ぎた時刻は、翌日から投稿されます。設定した直後に投稿されることはありません。動作確認には数分後の時刻を設定してください。
  - `/set_channel` を実行した直後も同様に、今日すでに過ぎた時刻には投稿されません。
- `/show_settings`（要サーバー管理権限）
  - そのサーバーの自動投稿先・投稿時刻・レア確率を表示します。
- `/set_rare_chance`（要サーバー管理権限）
  - 自動投稿がレア版になる確率を、0〜100の1%単位で設定します。例: `/set_rare_chance percent:5`
  - デフォルトは1%です。
- `/わさび`
  - 「くっだらねえ！なんだよそれ！わっかわっかしい！」と返します。
- `/わさびを投稿する`（デバッグ用・要サーバー管理権限）
  - 時刻に関係なく、そのサーバーの自動投稿先へ即座に「わさび」を投稿します（レア確率の抽選もあります）。
- キーワード自動応答（メッセージの先頭が一致したときだけ反応します）
  - `わさび…` →「くっだらねえ！なんだよそれ！わっかわっかしい！」
  - `わらび餅…` →「わさび餅」
  - Bot自身を含め、Botの発言には反応しません。
- 指定時刻になると、自動投稿先に「わさび」と投稿します（デフォルトは0時・6時・12時の1日3回）。
  - 設定したレア確率で「本わさび」と投稿されます。レア版ではボタンの返信も最後までレア版になります。
  - 自動投稿先・投稿時刻・レア確率はサーバーごとに独立しています。あるサーバーの設定を変えても、他のサーバーには影響しません。
- ボタンの進行フロー
  - `[食べる]` → ボタン削除 →「鼻がツーンとした！」+ `[ティッシュにぺ！ってする]`
  - `[ティッシュにぺ！ってする]` → ボタン削除 →「お前は許されないことした！」+ `[下ネタを聞く]`
  - `[下ネタを聞く]` → ボタン削除 →「すまん！悪かった！」

Botトークンは `.env` から読み込みます。自動投稿先は `/set_channel` で、投稿時刻は `/set_times` で、レア確率は `/set_rare_chance` で設定します。いずれもサーバーごとに `config.json` に保存され、Botを再起動しても引き継がれます。

## 事前準備

Discord Developer PortalでBotを作成し、Botトークンを取得してください。Botをサーバーへ招待するときは、以下の権限を付与してください。

- `Send Messages`
- `Use Slash Commands`
- `Read Message History`

`/set_channel`・`/set_times`・`/set_rare_chance`・`/show_settings`・`/わさびを投稿する` は、サーバー管理権限を持つユーザーのみサーバー内で実行できます（DMでは実行できません）。

### Message Content Intent の有効化

キーワード自動応答にはメッセージ本文の読み取り権限が必要です。Discord Developer Portalで対象Botの `Bot` タブを開き、`Privileged Gateway Intents` の **MESSAGE CONTENT INTENT** をONにしてください。

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
uv run wasabi-bot
```

macOS / Linux:

```bash
cp .env.example .env
nano .env
uv run wasabi-bot
```

2回目以降は `uv run wasabi-bot` だけです。Pythonのバージョンは `.python-version`、依存のバージョンは `uv.lock` で固定されているので、別のマシンでも同じ環境が再現されます。

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
uv run --no-dev wasabi-bot
```

`--frozen` はロックファイルを更新せずにそのまま使う指定です。開発環境に戻すときは `uv sync` を実行してください。

## Botの使い方

1. Botを起動します。
2. 自動投稿したいチャンネルで `/set_channel` を実行します。
3. デフォルトでは0時・6時・12時に、設定したチャンネルへ「わさび」が投稿されます。
4. 投稿時刻を変更したい場合は `/set_times` を実行してください（Botの再起動は不要です）。
5. 現在の設定は `/show_settings` で確認できます。

複数のサーバーで使う場合は、サーバーごとに手順2〜4を行ってください。Botがサーバーから外されると、そのサーバーの設定は削除されます。

投稿時刻は、Botを動かしている環境のローカル時刻で判定されます。

## ファイル構成

```text
.
├── src/
│   └── wasabi_bot/
│       ├── __init__.py
│       ├── bot.py          # Discord まわり（起動・ボタン・コマンド・定期投稿）
│       ├── config.py       # config.json の読み書き
│       ├── post_times.py   # 投稿時刻のパース・判定
│       └── replies.py      # キーワード応答
├── tests/
│   ├── conftest.py
│   ├── test_bot.py
│   ├── test_config.py
│   ├── test_post_times.py
│   └── test_replies.py
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── config.json.example
├── .gitignore
└── README.md
```

`config.json` はBot実行時に自動作成されます。`config.json.example` はその設定例です。`guilds` の下にサーバーIDごとの設定が入ります（例では投稿時刻を1日4回にしています）。設定はすべてスラッシュコマンドで変更できるので、手動で編集する必要はありません。`.env` と `config.json` は `.gitignore` に含まれているため、Gitにはコミットされません。`uv.lock` は依存バージョンを固定するためのファイルなので、Gitにコミットします。
