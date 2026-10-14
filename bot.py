import asyncio
import copy
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
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
    "ところてん": "つるつるてん🧑‍🦲",
    "わらび餅": "わさび餅",
    "わさび山": "さっさと完成させろ、このゴミカス!",
    "わさび": WASABI_REPLY,
    "わだび": "もしかして: __わさび__",
    "わさに": "もしかして: __わさび__",
    "ｑさび": "もしかして: __わさび__",
}

DEFAULT_GUILD_CONFIG: dict[str, Any] = {
    "channel_id": None,
    "post_times": ["00:00", "12:00", "06:00"],
    "last_post_date": None,
    "last_posted_times": [],
}

# 1日にこれ以上投稿させないための上限（連投防止）
MAX_POST_TIMES = 24
# \d は全角数字にも一致するので [0-9] で半角に限定する
POST_TIME_PATTERN = re.compile(r"([0-9]{1,2}):([0-9]{2})")


class ChannelNotMessageable(Exception):
    """自動投稿先がメッセージを送れないチャンネル（カテゴリ等）だったときに投げる。"""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wasabi-bot")


def empty_config() -> dict[str, Any]:
    return {"guilds": {}}


def quarantine_config(label: str, reason: object) -> dict[str, Any]:
    # 読めない設定を空として扱ったまま保存すると全サーバー分が消えるので、先に退避する。
    # 退避に失敗した場合は OSError をそのまま上げ、呼び出し側に保存させない。
    backup_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{label}-{datetime.now():%Y%m%d-%H%M%S}")  # noqa: DTZ005
    CONFIG_PATH.rename(backup_path)
    logger.warning("config.json could not be used (%s). Moved it to %s.", reason, backup_path.name)
    return empty_config()


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        config = empty_config()
        try:
            save_config(config)
        except OSError as error:
            # 設定は保存されないが、起動して応答だけはできるようにする
            logger.warning("Failed to create config.json. Settings will not persist: %s", error)
        return config

    # 退避（rename）する前にファイルを閉じておく必要がある（Windowsでは開いたままだと失敗する）
    text = CONFIG_PATH.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as error:
        return quarantine_config("broken", error)

    if not isinstance(loaded, dict):
        return quarantine_config("broken", "not a JSON object")
    if not isinstance(loaded.get("guilds"), dict):
        # サーバーごとの設定に対応する前の形式。移行はせず、各サーバーで設定し直してもらう
        return quarantine_config("legacy", "old single-server format")

    return {"guilds": loaded["guilds"]}


def get_guild_config(config: dict[str, Any], guild_id: int | str) -> dict[str, Any]:
    stored = config["guilds"].get(str(guild_id))
    if not isinstance(stored, dict):
        stored = {}
    return copy.deepcopy({**DEFAULT_GUILD_CONFIG, **stored})


def update_guild_config(guild_id: int | str, **changes: Any) -> None:
    # load から save まで await を挟まないので、他の処理が割り込んで上書きすることはない
    config = load_config()
    config["guilds"][str(guild_id)] = {**get_guild_config(config, guild_id), **changes}
    save_config(config)


def remove_guild_config(guild_id: int | str) -> None:
    config = load_config()
    if config["guilds"].pop(str(guild_id), None) is not None:
        save_config(config)


def posted_times_for_today(guild_config: dict[str, Any], today: str) -> list[str]:
    # 日付が変わったら「今日投稿済みの時刻」はリセットする
    if guild_config.get("last_post_date") != today:
        return []
    return list(guild_config.get("last_posted_times") or [])


def parse_post_times(text: str) -> list[str]:
    tokens = [token for token in re.split(r"[,、\s]+", text) if token]
    if not tokens:
        raise ValueError("投稿時刻が指定されていません。")

    times: set[str] = set()
    for token in tokens:
        match = POST_TIME_PATTERN.fullmatch(token)
        if match is None or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError(f"時刻の形式が正しくありません: {token}（例: 09:30）")
        times.add(f"{int(match[1]):02d}:{match[2]}")

    if len(times) > MAX_POST_TIMES:
        raise ValueError(f"投稿時刻は{MAX_POST_TIMES}件までです。")
    return sorted(times)


def times_already_passed(times: list[str], now: datetime) -> list[str]:
    # daily_post の投稿条件（scheduled <= now）とそろえ、ちょうど今の時刻も「過ぎた」側に含める
    return [
        post_time
        for post_time in times
        if datetime.combine(now.date(), datetime_time.fromisoformat(post_time)) <= now
    ]


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
        # (guild_id, 日付, 時刻)
        self.last_posted: set[tuple[str, str, str]] = set()

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

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        # 一時的な障害では on_guild_unavailable が呼ばれるだけなので、ここに来るのはキック・脱退時のみ
        try:
            remove_guild_config(guild.id)
        except OSError:
            logger.exception("Failed to remove settings for guild %s", guild.id)
        else:
            logger.info("Removed settings for guild %s", guild.id)

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
        mention_content = f"{interaction.user.mention}が押しました\n{content}"
        if view is None:
            await interaction.followup.send(mention_content)
        else:
            await interaction.followup.send(mention_content, view=view)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        message = "このコマンドにはサーバー管理権限が必要です。"
    else:
        logger.exception("Slash command failed", exc_info=error)
        message = "エラーが発生しました。ログを確認してください。"

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Failed to send error message for slash command")


@bot.tree.command(name="set_channel", description="このチャンネルを自動投稿先に設定します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_channel(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None or interaction.channel_id is None:
        await interaction.response.send_message(
            "チャンネルIDを取得できませんでした。",
            ephemeral=True,
        )
        return

    update_guild_config(interaction.guild_id, channel_id=interaction.channel_id)

    await interaction.response.send_message(
        f"自動投稿先を <#{interaction.channel_id}> に設定しました。",
        ephemeral=True,
    )


@bot.tree.command(name="set_times", description="自動投稿の時刻を設定します。")
@app_commands.describe(times="投稿時刻をカンマ区切りで指定（例: 00:00, 12:00, 18:30）")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_times(interaction: discord.Interaction, times: str) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("サーバーIDを取得できませんでした。", ephemeral=True)
        return

    try:
        post_times = parse_post_times(times)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    # daily_post と同じ方法で「今」を決める（README参照）
    now = datetime.now()  # noqa: DTZ005
    today = now.date().isoformat()
    # 今日すでに過ぎた時刻は投稿済み扱いにして、設定した直後に投稿されないようにする
    passed_times = times_already_passed(post_times, now)
    guild_config = get_guild_config(load_config(), interaction.guild_id)
    update_guild_config(
        interaction.guild_id,
        post_times=post_times,
        last_post_date=today,
        last_posted_times=sorted({*posted_times_for_today(guild_config, today), *passed_times}),
    )

    message = f"投稿時刻を {', '.join(post_times)} に設定しました。"
    if passed_times:
        message += f"\n今日すでに過ぎた時刻（{', '.join(passed_times)}）は明日から投稿されます。"
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="show_settings", description="このサーバーの自動投稿の設定を表示します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def show_settings(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("サーバーIDを取得できませんでした。", ephemeral=True)
        return

    guild_config = get_guild_config(load_config(), interaction.guild_id)
    channel_id = guild_config["channel_id"]
    channel_text = f"<#{channel_id}>" if channel_id else "未設定（/set_channel で設定してください）"
    times_text = ", ".join(guild_config["post_times"] or []) or "なし"

    await interaction.response.send_message(
        f"自動投稿先: {channel_text}\n投稿時刻: {times_text}\n※ 時刻はBotを動かしている環境のローカル時刻です。",
        ephemeral=True,
    )


@bot.tree.command(name="わさび", description="わさび")
async def wasabi(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(WASABI_REPLY)


async def send_wasabi_post(client: WasabiBot, channel_id: int) -> None:
    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise ChannelNotMessageable(f"Configured channel is not messageable: {channel_id}")

    await channel.send("わさび", view=WasabiView())
    # 前回分のボタン記録を捨てる。押下済みメッセージのボタンは既に消えているので、
    # 残しておく意味があるのは「押下直後の同時押し」を弾く数秒間だけ。
    client.processed_button_messages.clear()
    client.button_locks.clear()


@bot.tree.command(name="わさびを投稿する", description="デバッグ用: 自動投稿メッセージを今すぐ送信します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def post_now(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("サーバーIDを取得できませんでした。", ephemeral=True)
        return

    channel_id = get_guild_config(load_config(), interaction.guild_id)["channel_id"]
    if not channel_id:
        await interaction.response.send_message(
            "自動投稿先が設定されていません。先に /set_channel を実行してください。",
            ephemeral=True,
        )
        return

    try:
        await send_wasabi_post(bot, int(channel_id))
    except (ValueError, ChannelNotMessageable, discord.HTTPException):
        logger.exception("Failed to send debug post to channel %s", channel_id)
        await interaction.response.send_message("投稿に失敗しました。ログを確認してください。", ephemeral=True)
        return

    await interaction.response.send_message("投稿しました。", ephemeral=True)


async def post_for_guild(
    client: WasabiBot,
    guild_id: str,
    guild_config: dict[str, Any],
    now: datetime,
    today: str,
) -> None:
    channel_id = guild_config["channel_id"]
    post_times = guild_config["post_times"] or []
    if not channel_id or not post_times:
        return

    try:
        guild = client.get_guild(int(guild_id))
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        logger.warning("Invalid guild_id or channel_id in config.json: %s / %s", guild_id, channel_id)
        return

    # 脱退済みのサーバー（on_guild_remove を取りこぼした場合など）には投稿しない
    if guild is None:
        return

    posted_times = posted_times_for_today(guild_config, today)

    due_time: str | None = None
    for post_time in post_times:
        try:
            scheduled_time = datetime_time.fromisoformat(post_time)
        except ValueError:
            logger.warning("Configured post_time is invalid in guild %s, skipping: %s", guild_id, post_time)
            continue

        scheduled_datetime = datetime.combine(now.date(), scheduled_time)
        if (
            scheduled_datetime <= now < scheduled_datetime + POST_GRACE_PERIOD
            and post_time not in posted_times
            and (guild_id, today, post_time) not in client.last_posted
        ):
            due_time = post_time
            break

    if due_time is None:
        return

    try:
        await send_wasabi_post(client, channel_id)
    except (discord.NotFound, discord.Forbidden, ChannelNotMessageable) as error:
        # 再試行しても失敗し続ける原因なので、猶予期間中にログが出続けないよう今回の枠は諦める
        client.last_posted.add((guild_id, today, due_time))
        logger.warning(
            "Cannot post to channel %s in guild %s, skipping %s: %s",
            channel_id,
            guild_id,
            due_time,
            error,
        )
        return
    except discord.HTTPException:
        # 一時的な障害の可能性があるので、猶予期間内なら次の周回で再試行する
        logger.exception("Failed to send daily message to channel %s in guild %s", channel_id, guild_id)
        return

    # 保存が失敗しても同じ枠を再投稿しないよう、保存より先にメモリへ記録する
    client.last_posted.add((guild_id, today, due_time))
    logger.info("Posted daily wasabi message to channel %s in guild %s (%s)", channel_id, guild_id, due_time)

    try:
        # send を待っている間に /set_times 等で更新されている可能性があるので読み直す
        current = get_guild_config(load_config(), guild_id)
        update_guild_config(
            guild_id,
            last_post_date=today,
            last_posted_times=sorted({*posted_times_for_today(current, today), due_time}),
        )
    except OSError:
        logger.exception("Failed to update config.json after posting (guild %s)", guild_id)


@tasks.loop(seconds=30)
async def daily_post(client: WasabiBot) -> None:
    try:
        config = load_config()
    except OSError:
        # ここで例外を漏らすとループ自体が止まり、全サーバーの投稿が停止する
        logger.exception("Failed to load config.json. Skipping this round.")
        return

    # 投稿時刻はBotを動かしている環境のローカル時刻で判定する（README参照）
    now = datetime.now()  # noqa: DTZ005
    today = now.date().isoformat()
    client.last_posted = {entry for entry in client.last_posted if entry[1] == today}

    for guild_id in config["guilds"]:
        await post_for_guild(client, guild_id, get_guild_config(config, guild_id), now, today)


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
