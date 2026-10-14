import asyncio
import logging
import os
import time
from datetime import datetime
from datetime import time as datetime_time
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from wasabi_bot.config import (
    get_guild_config,
    load_config,
    remove_guild_config,
    update_guild_config,
)
from wasabi_bot.post_times import (
    POST_GRACE_PERIOD,
    mark_passed_times_as_posted,
    parse_post_times,
    posted_times_for_today,
    times_already_passed,
)
from wasabi_bot.replies import WASABI_REPLY, find_reply

# 押下済みボタンの記録を残す秒数。弾きたいのは押下直後の同時押しだけなので短くてよい
BUTTON_DEDUP_SECONDS = 600


class ChannelNotMessageable(Exception):
    """自動投稿先がメッセージを送れないチャンネル（カテゴリ等）だったときに投げる。"""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wasabi-bot")


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
        # message_id -> 押下時刻（time.monotonic()）
        self.processed_button_messages: dict[int, float] = {}
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

    now = time.monotonic()
    client.processed_button_messages = {
        processed_id: pressed_at
        for processed_id, pressed_at in client.processed_button_messages.items()
        if now - pressed_at < BUTTON_DEDUP_SECONDS
    }

    message_id = interaction.message.id
    lock = client.button_locks.setdefault(message_id, asyncio.Lock())

    try:
        async with lock:
            if message_id in client.processed_button_messages:
                await interaction.response.send_message(
                    "このボタンはすでに押されています。",
                    ephemeral=True,
                )
                return

            # edit_message が失敗したら処理済みにしない（ボタンが押せないまま死ぬのを防ぐ）
            await interaction.response.edit_message(view=None)
            client.processed_button_messages[message_id] = now
            mention_content = f"{interaction.user.mention}が押しました\n{content}"
            if view is None:
                await interaction.followup.send(mention_content)
            else:
                await interaction.followup.send(mention_content, view=view)
    finally:
        # 待機中の押下は同じ Lock を持っており、後から来た押下も processed で弾かれるので捨ててよい
        client.button_locks.pop(message_id, None)


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

    # daily_post と同じ方法で「今」を決める（README参照）
    now = datetime.now()  # noqa: DTZ005
    guild_config = get_guild_config(load_config(), interaction.guild_id)
    update_guild_config(
        interaction.guild_id,
        channel_id=interaction.channel_id,
        **mark_passed_times_as_posted(guild_config, guild_config["post_times"] or [], now),
    )

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
    passed_times = times_already_passed(post_times, now)
    guild_config = get_guild_config(load_config(), interaction.guild_id)
    update_guild_config(
        interaction.guild_id,
        post_times=post_times,
        **mark_passed_times_as_posted(guild_config, post_times, now),
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

    client = interaction.client
    if not isinstance(client, WasabiBot):
        await interaction.response.send_message("Botの状態を取得できませんでした。", ephemeral=True)
        return

    # fetch_channel や送信が3秒を超えると応答が失効し、成功しても失敗表示になって再実行を招くので先に defer する
    await interaction.response.defer(ephemeral=True, thinking=True)

    try:
        await send_wasabi_post(client, int(channel_id))
    except (ValueError, ChannelNotMessageable, discord.HTTPException):
        logger.exception("Failed to send debug post to channel %s", channel_id)
        await interaction.followup.send("投稿に失敗しました。ログを確認してください。", ephemeral=True)
        return

    await interaction.followup.send("投稿しました。", ephemeral=True)


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
