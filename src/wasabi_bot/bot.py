import logging
import os
import random
import time
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from wasabi_bot.config import (
    get_guild_config,
    load_config,
    rare_percent,
    remove_guild_config,
    update_guild_config,
)
from wasabi_bot.post_times import (
    POST_GRACE_PERIOD,
    is_slot_posted,
    mark_passed_times_as_posted,
    parse_post_times,
    posted_times_for_today,
    times_already_passed,
)
from wasabi_bot.replies import (
    DIRTY_JOKE_REPLY,
    EAT_REPLY,
    TISSUE_REPLY,
    WASABI_POST,
    WASABI_REPLY,
    find_reply,
    pick_message,
)

# 押下済みボタンの記録を残す秒数。弾きたいのは押下直後の同時押しだけなので短くてよい
BUTTON_DEDUP_SECONDS = 600

# Botのステータスに表示する文言。rotate_presence がここからランダムに選ぶ
PRESENCE_TEXTS = [
    "わさびをプレイ中",
    "わさび山ってなんだろう",
]


class ChannelNotMessageable(Exception):
    """自動投稿先がメッセージを送れないチャンネル（カテゴリ等）だったときに投げる。"""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wasabi-bot")


class ButtonChainView(discord.ui.View):
    """rare=True のときはボタンの返信をすべてレア版にする。"""

    def __init__(self, rare: bool = False) -> None:
        super().__init__(timeout=None)
        self.rare = rare
        # Bot再起動後もどちらの版か区別できるよう、custom_id を分ける
        if rare:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id:
                    item.custom_id += ":rare"


class WasabiView(ButtonChainView):

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
            pick_message(EAT_REPLY, rare=self.rare),
            TissueView(rare=self.rare),
        )


class TissueView(ButtonChainView):

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
            pick_message(TISSUE_REPLY, rare=self.rare),
            DirtyJokeView(rare=self.rare),
        )


class DirtyJokeView(ButtonChainView):

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
        await send_next_once(interaction, pick_message(DIRTY_JOKE_REPLY, rare=self.rare))


class WasabiBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        # message_id -> 押下時刻（time.monotonic()）
        self.processed_button_messages: dict[int, float] = {}
        # config.json への保存が失敗しても同じ枠を再投稿しないためのメモリ上の記録
        # (guild_id, 日付, 時刻)
        self.last_posted: set[tuple[str, str, str]] = set()
        # 同じ文言が連続しないよう、直前に表示したステータス文言を覚えておく
        self.presence_text: str | None = None

    async def setup_hook(self) -> None:
        for rare in (False, True):
            self.add_view(WasabiView(rare=rare))
            self.add_view(TissueView(rare=rare))
            self.add_view(DirtyJokeView(rare=rare))

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
        if not rotate_presence.is_running():
            rotate_presence.start(self)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "unknown")

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
                await message.reply(reply)
            except discord.Forbidden:
                logger.warning("Missing permission to reply in channel %s", message.channel.id)


bot = WasabiBot()


def release_sent_view(view: discord.ui.View) -> None:
    # 送信時の View はメッセージごとに登録され、timeout=None なので自然には消えない。
    # 押下は setup_hook で登録した永続 View が custom_id で処理する（再起動後と同じ経路）ので、ここで登録を外す
    view.stop()


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
    if message_id in client.processed_button_messages:
        await interaction.response.send_message(
            "このボタンはすでに押されています。",
            ephemeral=True,
        )
        return

    # 同時押しを弾くため await より先に印を付ける（チェックとの間に await がないので割り込まれない）
    client.processed_button_messages[message_id] = now
    try:
        await interaction.response.edit_message(view=None)
    except Exception:
        # ボタンは残っているので、押し直せるよう印を外す（待機中に付け直された別の押下の印は消さない）
        if client.processed_button_messages.get(message_id) == now:
            client.processed_button_messages.pop(message_id)
        raise

    mention_content = f"{interaction.user.mention}が押しました\n{content}"
    if view is None:
        await interaction.followup.send(mention_content)
    else:
        await interaction.followup.send(mention_content, view=view)
        release_sent_view(view)


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
        f"自動投稿先: {channel_text}\n投稿時刻: {times_text}\nレア確率: {rare_percent(guild_config)}%\n"
        "※ 時刻はBotを動かしている環境のローカル時刻です。",
        ephemeral=True,
    )


@bot.tree.command(name="set_rare_chance", description="自動投稿がレア版になる確率を設定します。")
@app_commands.describe(percent="レアになる確率（%、0〜100）")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_rare_chance(interaction: discord.Interaction, percent: app_commands.Range[int, 0, 100]) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("サーバーIDを取得できませんでした。", ephemeral=True)
        return

    update_guild_config(interaction.guild_id, rare_percent=percent)
    await interaction.response.send_message(f"レア確率を {percent}% に設定しました。", ephemeral=True)


@bot.tree.command(name="わさび", description="わさび")
async def wasabi(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(WASABI_REPLY)


def roll_rare(percent: int) -> bool:
    return random.random() < percent / 100


async def send_wasabi_post(client: WasabiBot, channel_id: int, rare: bool = False) -> None:
    channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise ChannelNotMessageable(f"Configured channel is not messageable: {channel_id}")

    # 抽選は呼び出し側で投稿時に1回だけ行う。レア版が出たらボタンの返信も最後までレア版になる
    view = WasabiView(rare=rare)
    await channel.send(pick_message(WASABI_POST, rare=rare), view=view)
    release_sent_view(view)


@bot.tree.command(name="わさびを投稿する", description="デバッグ用: 自動投稿メッセージを今すぐ送信します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def post_now(interaction: discord.Interaction) -> None:
    await post_debug_wasabi(interaction, rare=False)


@bot.tree.command(name="レアわさびを投稿する", description="デバッグ用: レア版の自動投稿メッセージを今すぐ送信します。")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def post_rare_now(interaction: discord.Interaction) -> None:
    await post_debug_wasabi(interaction, rare=True)


async def post_debug_wasabi(interaction: discord.Interaction, rare: bool) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("サーバーIDを取得できませんでした。", ephemeral=True)
        return

    guild_config = get_guild_config(load_config(), interaction.guild_id)
    channel_id = guild_config["channel_id"]
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
        await send_wasabi_post(client, int(channel_id), rare=rare or roll_rare(rare_percent(guild_config)))
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

    # 各時刻の直近の枠（まだ来ていなければ前日の枠）を見る。猶予期間が日付をまたいでも（23:58 の枠を 0:01 に）投稿できる
    due: tuple[datetime, str, str] | None = None
    for post_time in post_times:
        try:
            scheduled_time = datetime_time.fromisoformat(post_time)
        except (TypeError, ValueError):
            logger.warning("Configured post_time is invalid in guild %s, skipping: %s", guild_id, post_time)
            continue

        scheduled_datetime = datetime.combine(now.date(), scheduled_time)
        if scheduled_datetime > now:
            scheduled_datetime -= timedelta(days=1)
        day = scheduled_datetime.date().isoformat()
        if (
            now < scheduled_datetime + POST_GRACE_PERIOD
            and not is_slot_posted(guild_config, day, post_time, today)
            and (guild_id, day, post_time) not in client.last_posted
            and (due is None or scheduled_datetime < due[0])
        ):
            # 古い枠から順に投稿する（前日の 23:58 を今日の 0:00 より先に）
            due = (scheduled_datetime, day, post_time)

    if due is None:
        return
    _, day, due_time = due

    try:
        # 不正な rare_percent の警告が周回ごとに出ないよう、投稿するときだけ解釈する
        await send_wasabi_post(client, channel_id, rare=roll_rare(rare_percent(guild_config)))
    except (discord.NotFound, discord.Forbidden, ChannelNotMessageable) as error:
        # 再試行しても失敗し続ける原因なので、猶予期間中にログが出続けないよう今回の枠は諦める
        client.last_posted.add((guild_id, day, due_time))
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
    client.last_posted.add((guild_id, day, due_time))
    logger.info("Posted daily wasabi message to channel %s in guild %s (%s)", channel_id, guild_id, due_time)

    try:
        # send を待っている間に /set_times 等で更新されている可能性があるので読み直す
        current = get_guild_config(load_config(), guild_id)
        if day != today and current["last_post_date"] == today:
            # 今日の記録を前日の記録で上書きすると今日の枠が再投稿されるので保存しない（前日の枠は今日の記録で投稿済み扱いになる）
            return
        update_guild_config(
            guild_id,
            last_post_date=day,
            last_posted_times=sorted({*posted_times_for_today(current, day), due_time}),
        )
    except OSError:
        logger.exception("Failed to update config.json after posting (guild %s)", guild_id)


@tasks.loop(seconds=30)
async def daily_post(client: WasabiBot) -> None:
    try:
        config = load_config()
    except (OSError, ValueError):
        # UnicodeDecodeError は ValueError なので OSError だけでは捕まらない
        # ここで例外を漏らすとループ自体が止まり、全サーバーの投稿が停止する
        logger.exception("Failed to load config.json. Skipping this round.")
        return

    # 投稿時刻はBotを動かしている環境のローカル時刻で判定する（README参照）
    now = datetime.now()  # noqa: DTZ005
    today = now.date().isoformat()
    # 日付をまたいだ猶予期間中の前日の枠も二重投稿しないよう、前日分まで残す
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    client.last_posted = {entry for entry in client.last_posted if entry[1] in (today, yesterday)}

    for guild_id in config["guilds"]:
        try:
            await post_for_guild(client, guild_id, get_guild_config(config, guild_id), now, today)
        except Exception:
            # tasks.loop は OSError 等以外の例外でループごと止まるので、1サーバーの不正設定で全サーバーを止めない
            logger.exception("Unexpected error while posting for guild %s", guild_id)


@daily_post.error
async def daily_post_error(error: Exception) -> None:
    logger.exception("Daily post loop failed unexpectedly", exc_info=error)


@daily_post.before_loop
async def before_daily_post() -> None:
    await bot.wait_until_ready()


@tasks.loop(minutes=30)
async def rotate_presence(client: WasabiBot) -> None:
    candidates = [text for text in PRESENCE_TEXTS if text != client.presence_text] or PRESENCE_TEXTS
    text = random.choice(candidates)
    try:
        await client.change_presence(activity=discord.CustomActivity(name=text))
    except Exception:
        # tasks.loop は OSError 等以外の例外でループごと止まるので、ここで止めずに次の回に任せる
        logger.exception("Failed to change presence")
        return
    client.presence_text = text


@rotate_presence.error
async def rotate_presence_error(error: Exception) -> None:
    logger.exception("Presence loop failed unexpectedly", exc_info=error)


@rotate_presence.before_loop
async def before_rotate_presence() -> None:
    await bot.wait_until_ready()


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Create .env from .env.example.")

    bot.run(token)


if __name__ == "__main__":
    main()
