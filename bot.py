import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.json"

WASABI_REPLY = "くっだらねえ！なんだよそれ！わっかわっかしい！"

# ループの遅延（Discord API待ちやファイルI/Oでの一時的なブロッキング等）を吸収しつつ、
# 予定時刻を過ぎた状態でBotを再起動したときに即座に投稿してしまわないための猶予
POST_GRACE_PERIOD = timedelta(minutes=5)

# 前方一致で判定するので、長いキーワードを先に置く（dictは挿入順を保つ）
KEYWORD_REPLIES = {
    "わらび餅": "わさび餅",
    "わさび": WASABI_REPLY,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "channel_id": None,
    "post_times": ["00:00", "12:00"],
    "last_post_date": None,
    "last_posted_times": [],
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wasabi-bot")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        try:
            save_config(DEFAULT_CONFIG)
        except OSError as error:
            # 設定は保存されないが、起動して応答だけはできるようにする
            logger.warning("Failed to create config.json. Settings will not persist: %s", error)
        return DEFAULT_CONFIG.copy()

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        logger.warning("Failed to load config.json. Using defaults: %s", error)
        return DEFAULT_CONFIG.copy()

    if not isinstance(loaded, dict):
        logger.warning("config.json is not a JSON object. Using defaults.")
        return DEFAULT_CONFIG.copy()

    return {**DEFAULT_CONFIG, **loaded}


def find_reply(content: str) -> str | None:
    for keyword, reply in KEYWORD_REPLIES.items():
        if content.startswith(keyword):
            return reply
    return None


def save_config(config: dict[str, Any]) -> None:
    temp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")

    # Windowsではエディタやウイルス対策ソフトが一瞬ファイルをロックすることがあり、
    # replace()が失敗すると投稿記録が保存されず再投稿の原因になるため短時間リトライする
    for attempt in range(5):
        try:
            temp_path.replace(CONFIG_PATH)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.2)


class WasabiView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="食べる",
        style=discord.ButtonStyle.success,
        custom_id="wasabi:eat",
    )
    async def eat(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await send_next_once(
            interaction,
            "鼻がツーンとした！",
            TissueView(),
        )


class TissueView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ティッシュにぺ！ってする",
        style=discord.ButtonStyle.primary,
        custom_id="wasabi:tissue",
    )
    async def tissue(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await send_next_once(
            interaction,
            "お前は許されないことした！",
            DirtyJokeView(),
        )


class DirtyJokeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="下ネタを聞く",
        style=discord.ButtonStyle.danger,
        custom_id="wasabi:dirty_joke",
    )
    async def dirty_joke(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await send_next_once(interaction, "すまん！悪かった！")


class WasabiBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.button_locks: dict[int, asyncio.Lock] = {}
        self.processed_button_messages: set[int] = set()
        # config.json への保存が失敗しても同じ枠を再投稿しないためのメモリ上の記録
        self.last_posted: set[tuple[str, str]] = set()

    async def setup_hook(self) -> None:
        self.add_view(WasabiView())
        self.add_view(TissueView())
        self.add_view(DirtyJokeView())

        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
            except ValueError:
                logger.warning("DISCORD_GUILD_ID must be a numeric Discord server ID. Syncing globally.")
                await self.tree.sync()
            else:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                logger.info("Synced slash commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global slash commands")

        if not daily_post.is_running():
            daily_post.start(self)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "unknown")
        await self.change_presence(activity=discord.Game(name="わさび"))

    async def on_message(self, message: discord.Message) -> None:
        # 自動投稿の「わさび」に自分で反応しないよう、Bot発言は全て無視する
        if message.author.bot:
            return

        reply = find_reply(message.content)
        if reply:
            try:
                await message.channel.send(reply)
            except discord.Forbidden:
                logger.warning("Missing permission to reply in channel %s", message.channel.id)


bot = WasabiBot()


async def send_next_once(
    interaction: discord.Interaction,
    content: str,
    view: discord.ui.View | None = None,
) -> None:
    if interaction.message is None:
        await interaction.response.send_message(
            "対象メッセージを取得できませんでした。",
            ephemeral=True,
        )
        return

    client = interaction.client
    if not isinstance(client, WasabiBot):
        await interaction.response.send_message(
            "Botの状態を取得できませんでした。",
            ephemeral=True,
        )
        return

    message_id = interaction.message.id
    lock = client.button_locks.setdefault(message_id, asyncio.Lock())

    async with lock:
        if message_id in client.processed_button_messages:
            await interaction.response.send_message(
                "このボタンはすでに押されています。",
                ephemeral=True,
            )
            return

        # edit_message が失敗したら処理済みにしない（ボタンが押せないまま死ぬのを防ぐ）
        await interaction.response.edit_message(view=None)
        client.processed_button_messages.add(message_id)
        if view is None:
            await interaction.followup.send(content)
        else:
            await interaction.followup.send(content, view=view)


@bot.tree.command(name="set_channel", description="このチャンネルを自動投稿先に設定します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def set_channel(interaction: discord.Interaction) -> None:
    if interaction.channel_id is None:
        await interaction.response.send_message(
            "チャンネルIDを取得できませんでした。",
            ephemeral=True,
        )
        return

    config = load_config()
    config["channel_id"] = interaction.channel_id
    save_config(config)

    await interaction.response.send_message(
        f"自動投稿先を <#{interaction.channel_id}> に設定しました。",
        ephemeral=True,
    )


@bot.tree.command(name="わさび", description="わさび")
async def wasabi(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(WASABI_REPLY)


async def send_wasabi_post(client: WasabiBot, channel_id: int) -> None:
    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise TypeError(f"Configured channel is not messageable: {channel_id}")

    await channel.send("わさび", view=WasabiView())
    # 前回分のボタン記録を捨てる。押下済みメッセージのボタンは既に消えているので、
    # 残しておく意味があるのは「押下直後の同時押し」を弾く数秒間だけ。
    client.processed_button_messages.clear()
    client.button_locks.clear()


@bot.tree.command(name="わさびを投稿する", description="デバッグ用: 自動投稿メッセージを今すぐ送信します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def post_now(interaction: discord.Interaction) -> None:
    config = load_config()
    channel_id = config.get("channel_id")
    if not channel_id:
        await interaction.response.send_message("自動投稿先が設定されていません。", ephemeral=True)
        return

    try:
        await send_wasabi_post(bot, int(channel_id))
    except (TypeError, ValueError, discord.Forbidden, discord.HTTPException):
        logger.exception("Failed to send debug post to channel %s", channel_id)
        await interaction.response.send_message("投稿に失敗しました。ログを確認してください。", ephemeral=True)
        return

    await interaction.response.send_message("投稿しました。", ephemeral=True)


@tasks.loop(seconds=30)
async def daily_post(client: WasabiBot) -> None:
    config = load_config()
    channel_id = config.get("channel_id")
    post_times = config.get("post_times") or []

    if not channel_id or not post_times:
        return

    # 投稿時刻はBotを動かしている環境のローカル時刻で判定する（README参照）
    now = datetime.now()  # noqa: DTZ005
    today = date.today().isoformat()  # noqa: DTZ011

    # 日付が変わったら「今日投稿済みの時刻」はリセットする
    posted_times: list[str] = (
        config.get("last_posted_times") or [] if config.get("last_post_date") == today else []
    )
    client.last_posted = {entry for entry in client.last_posted if entry[0] == today}

    due_time: str | None = None
    for post_time in post_times:
        try:
            scheduled_time = datetime_time.fromisoformat(post_time)
        except ValueError:
            logger.warning("Configured post_time is invalid, skipping: %s", post_time)
            continue

        scheduled_datetime = datetime.combine(now.date(), scheduled_time)
        if (
            scheduled_datetime <= now < scheduled_datetime + POST_GRACE_PERIOD
            and post_time not in posted_times
            and (today, post_time) not in client.last_posted
        ):
            due_time = post_time
            break

    if due_time is None:
        return

    try:
        await send_wasabi_post(client, int(channel_id))
        client.last_posted.add((today, due_time))
        # send を待っている間に別の投稿処理が更新している可能性があるので読み直す
        config = load_config()
        config["last_post_date"] = today
        config["last_posted_times"] = sorted({*posted_times, due_time})
        save_config(config)
        logger.info("Posted daily wasabi message to channel %s (%s)", channel_id, due_time)
    except (TypeError, ValueError):
        logger.exception("Configured channel_id is invalid: %s", channel_id)
    except discord.Forbidden:
        logger.exception("Missing permission to send messages to channel %s", channel_id)
    except discord.HTTPException:
        logger.exception("Failed to send daily message to channel %s", channel_id)
    except OSError:
        logger.exception("Failed to update config.json after posting")


@daily_post.error
async def daily_post_error(error: Exception) -> None:
    logger.exception("Daily post loop failed unexpectedly", exc_info=error)


@daily_post.before_loop
async def before_daily_post() -> None:
    await bot.wait_until_ready()


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Create .env from .env.example.")

    bot.run(token)


if __name__ == "__main__":
    main()
