import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord

from wasabi_bot import bot as wasabi
from wasabi_bot import replies
from wasabi_bot.config import get_guild_config, load_config, update_guild_config


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.sent = []
        self.views = []

    async def _get_channel(self):
        return self

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        self.views.append(kwargs.get("view"))


def make_post_now_interaction(client):
    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.client = client
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_post_now_defers_before_sending(config_path, monkeypatch):
    update_guild_config(1, channel_id=111)
    client = wasabi.WasabiBot()
    interaction = make_post_now_interaction(client)
    calls = []
    interaction.response.defer.side_effect = lambda **kwargs: calls.append("defer")

    async def fake_send(client, channel_id, rare=False):
        calls.append(("send", channel_id, rare))

    monkeypatch.setattr(wasabi, "send_wasabi_post", fake_send)

    asyncio.run(wasabi.post_now.callback(interaction))

    # 送信に3秒以上かかっても応答が失効しないよう、先に defer してから followup で返す
    assert calls == ["defer", ("send", 111, False)]
    interaction.followup.send.assert_awaited_once_with("投稿しました。", ephemeral=True)
    interaction.response.send_message.assert_not_awaited()


def test_post_now_reports_failure_via_followup(config_path, monkeypatch):
    update_guild_config(1, channel_id=111)
    interaction = make_post_now_interaction(wasabi.WasabiBot())

    async def fail_send(client, channel_id, rare=False):
        raise wasabi.ChannelNotMessageable("category")

    monkeypatch.setattr(wasabi, "send_wasabi_post", fail_send)

    asyncio.run(wasabi.post_now.callback(interaction))

    interaction.followup.send.assert_awaited_once_with("投稿に失敗しました。ログを確認してください。", ephemeral=True)


def test_send_wasabi_post_keeps_button_state(monkeypatch):
    client = wasabi.WasabiBot()
    channel = FakeChannel()
    monkeypatch.setattr(client, "get_channel", lambda channel_id: channel)
    client.processed_button_messages[1] = 0.0

    monkeypatch.setattr(replies.random, "random", lambda: 0.5)

    asyncio.run(wasabi.send_wasabi_post(client, 111))

    # 別サーバーで押下処理中のボタンの重複チェックを壊さない
    assert channel.sent == ["わさび"]
    assert 1 in client.processed_button_messages


def test_post_rare_now_sends_rare_post(config_path, monkeypatch):
    update_guild_config(1, channel_id=111)
    interaction = make_post_now_interaction(wasabi.WasabiBot())
    calls = []

    async def fake_send(client, channel_id, rare=False):
        calls.append((channel_id, rare))

    monkeypatch.setattr(wasabi, "send_wasabi_post", fake_send)

    asyncio.run(wasabi.post_rare_now.callback(interaction))

    assert calls == [(111, True)]
    interaction.followup.send.assert_awaited_once_with("投稿しました。", ephemeral=True)


def test_send_wasabi_post_rare(monkeypatch):
    client = wasabi.WasabiBot()
    channel = FakeChannel()
    monkeypatch.setattr(client, "get_channel", lambda channel_id: channel)
    monkeypatch.setattr(replies.random, "random", lambda: 0.5)  # 抽選に関係なくレア版になる

    asyncio.run(wasabi.send_wasabi_post(client, 111, rare=True))

    assert channel.sent == ["本わさび"]
    assert channel.views[0].rare


def test_rare_views_use_separate_custom_ids():
    async def custom_ids(view_class, rare):
        return [item.custom_id for item in view_class(rare=rare).children]

    for view_class in (wasabi.WasabiView, wasabi.TissueView, wasabi.DirtyJokeView):
        normal = asyncio.run(custom_ids(view_class, False))
        rare = asyncio.run(custom_ids(view_class, True))
        assert rare == [f"{custom_id}:rare" for custom_id in normal]


def test_rare_button_chain_stays_rare(monkeypatch):
    sent = []

    async def fake_send_next_once(interaction, content, view=None):
        sent.append((content, view))

    monkeypatch.setattr(wasabi, "send_next_once", fake_send_next_once)
    monkeypatch.setattr(replies.random, "random", lambda: 0.5)

    async def run():
        view = wasabi.WasabiView(rare=True)
        await view.eat.callback(MagicMock())
        await sent[-1][1].tissue.callback(MagicMock())
        await sent[-1][1].dirty_joke.callback(MagicMock())

    asyncio.run(run())

    assert [content for content, _ in sent] == [
        replies.EAT_REPLY[1],
        replies.TISSUE_REPLY[1],
        replies.DIRTY_JOKE_REPLY[1],
    ]
    assert all(view.rare for _, view in sent[:2])


def test_send_wasabi_post_draws_rare_once(monkeypatch):
    client = wasabi.WasabiBot()
    channel = FakeChannel()
    monkeypatch.setattr(client, "get_channel", lambda channel_id: channel)

    monkeypatch.setattr(replies.random, "random", lambda: 0.0)
    asyncio.run(wasabi.send_wasabi_post(client, 111))
    monkeypatch.setattr(replies.random, "random", lambda: 0.5)
    asyncio.run(wasabi.send_wasabi_post(client, 111))

    # 抽選に当たったときだけ本わさびになり、ボタンの返信もレア版に固定される
    assert channel.sent == ["本わさび", "わさび"]
    assert [view.rare for view in channel.views] == [True, False]


def test_normal_button_chain_never_rare(monkeypatch):
    sent = []

    async def fake_send_next_once(interaction, content, view=None):
        sent.append(content)

    monkeypatch.setattr(wasabi, "send_next_once", fake_send_next_once)
    monkeypatch.setattr(replies.random, "random", lambda: 0.0)  # ボタンでは抽選しない

    asyncio.run(wasabi.WasabiView().eat.callback(MagicMock()))

    assert sent == [replies.EAT_REPLY[0]]


def test_daily_post_continues_after_guild_error(config_path, monkeypatch):
    update_guild_config(1, channel_id=111)
    update_guild_config(2, channel_id=222)
    processed = []

    async def fake_post_for_guild(client, guild_id, guild_config, now, today):
        if guild_id == "1":
            raise TypeError("broken post_times")
        processed.append(guild_id)

    monkeypatch.setattr(wasabi, "post_for_guild", fake_post_for_guild)

    # 例外が漏れると tasks.loop ごと止まり、全サーバーの自動投稿が止まる
    asyncio.run(wasabi.daily_post.coro(wasabi.WasabiBot()))

    assert processed == ["2"]


def test_daily_post_skips_undecodable_config(config_path):
    config_path.write_bytes('{"guilds": {"1": {"memo": "わさび"}}}'.encode("shift_jis"))

    # UnicodeDecodeError は OSError ではないので、別途捕まえないとループが止まる
    asyncio.run(wasabi.daily_post.coro(wasabi.WasabiBot()))


def make_button_interaction(client, message_id=10):
    interaction = MagicMock()
    interaction.client = client
    interaction.message.id = message_id
    interaction.user.mention = "<@1>"
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_send_next_once_rejects_press_during_edit():
    client = wasabi.WasabiBot()
    first = make_button_interaction(client)
    second = make_button_interaction(client)

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_edit(**kwargs):
            started.set()
            await release.wait()

        first.response.edit_message.side_effect = slow_edit
        task = asyncio.create_task(wasabi.send_next_once(first, "鼻がツーンとした！"))
        await started.wait()
        await wasabi.send_next_once(second, "鼻がツーンとした！")
        release.set()
        await task

    asyncio.run(run())

    # 1回目の edit_message 中に来た押下は弾かれ、次のメッセージは1回だけ送られる
    first.followup.send.assert_awaited_once()
    second.response.edit_message.assert_not_awaited()
    second.response.send_message.assert_awaited_once_with("このボタンはすでに押されています。", ephemeral=True)


def test_send_next_once_allows_retry_after_edit_failure():
    client = wasabi.WasabiBot()
    failed = make_button_interaction(client)
    failed.response.edit_message.side_effect = discord.HTTPException(MagicMock(status=500), "error")
    retry = make_button_interaction(client)

    async def run():
        try:
            await wasabi.send_next_once(failed, "鼻がツーンとした！")
        except discord.HTTPException:
            pass
        await wasabi.send_next_once(retry, "鼻がツーンとした！")

    asyncio.run(run())

    # ボタンは残っているので、押し直せば処理される
    failed.followup.send.assert_not_awaited()
    retry.followup.send.assert_awaited_once()


def test_sent_views_are_not_kept_registered(monkeypatch):
    client = wasabi.WasabiBot()
    channel = FakeChannel()
    monkeypatch.setattr(client, "get_channel", lambda channel_id: channel)
    interaction = make_button_interaction(client)

    async def run():
        await wasabi.send_wasabi_post(client, 111)
        await wasabi.send_next_once(interaction, "鼻がツーンとした！", wasabi.TissueView())

    asyncio.run(run())

    # 押下は永続 View が処理するので、送信した View はメッセージごとに登録を残さない
    assert channel.views[0].is_finished()
    assert interaction.followup.send.await_args.kwargs["view"].is_finished()


def local_time(*args):
    return datetime(*args)  # noqa: DTZ001  Botはローカル時刻で判定する


def run_post_for_guild(monkeypatch, client, guild_id, now):
    channel = FakeChannel()
    monkeypatch.setattr(client, "get_guild", lambda guild_id: object())
    monkeypatch.setattr(client, "get_channel", lambda channel_id: channel)
    today = now.date().isoformat()
    guild_config = get_guild_config(load_config(), guild_id)
    asyncio.run(wasabi.post_for_guild(client, str(guild_id), guild_config, now, today))
    return channel


def test_post_for_guild_posts_previous_day_slot_after_midnight(config_path, monkeypatch):
    update_guild_config(1, channel_id=111, post_times=["23:58"], last_post_date="2026-10-13", last_posted_times=[])
    client = wasabi.WasabiBot()

    channel = run_post_for_guild(monkeypatch, client, 1, local_time(2026, 10, 14, 0, 1))

    # 23:58 の猶予期間中に日付が変わっても投稿し、前日の枠として記録する
    assert len(channel.sent) == 1
    saved = get_guild_config(load_config(), 1)
    assert (saved["last_post_date"], saved["last_posted_times"]) == ("2026-10-13", ["23:58"])


def test_post_for_guild_posts_older_slot_first(config_path, monkeypatch):
    update_guild_config(1, channel_id=111, post_times=["00:00", "23:58"], last_post_date=None, last_posted_times=[])
    client = wasabi.WasabiBot()
    now = local_time(2026, 10, 14, 0, 1)

    run_post_for_guild(monkeypatch, client, 1, now)
    assert ("1", "2026-10-13", "23:58") in client.last_posted
    run_post_for_guild(monkeypatch, client, 1, now)
    assert ("1", "2026-10-14", "00:00") in client.last_posted

    saved = get_guild_config(load_config(), 1)
    assert (saved["last_post_date"], saved["last_posted_times"]) == ("2026-10-14", ["00:00"])


def test_post_for_guild_skips_previous_day_slot_when_today_recorded(config_path, monkeypatch):
    update_guild_config(1, channel_id=111, post_times=["23:58"], last_post_date="2026-10-14", last_posted_times=[])

    channel = run_post_for_guild(monkeypatch, wasabi.WasabiBot(), 1, local_time(2026, 10, 14, 0, 1))

    # /set_times 等で今日の記録があるなら、前日の枠は処理済み
    assert channel.sent == []


def test_post_for_guild_keeps_today_record_written_during_send(config_path, monkeypatch):
    update_guild_config(1, channel_id=111, post_times=["23:58"], last_post_date="2026-10-13", last_posted_times=[])
    client = wasabi.WasabiBot()

    async def send_while_set_times_runs(client, channel_id, rare=False):
        update_guild_config(1, last_post_date="2026-10-14", last_posted_times=["00:00"])

    monkeypatch.setattr(wasabi, "send_wasabi_post", send_while_set_times_runs)

    run_post_for_guild(monkeypatch, client, 1, local_time(2026, 10, 14, 0, 1))

    # 前日の記録で上書きすると今日の 00:00 が再投稿されるので、今日の記録を残す
    saved = get_guild_config(load_config(), 1)
    assert (saved["last_post_date"], saved["last_posted_times"]) == ("2026-10-14", ["00:00"])


def test_post_for_guild_ignores_future_last_post_date(config_path, monkeypatch):
    update_guild_config(1, channel_id=111, post_times=["12:00"], last_post_date="2026-12-31", last_posted_times=["12:00"])

    channel = run_post_for_guild(monkeypatch, wasabi.WasabiBot(), 1, local_time(2026, 10, 14, 12, 1))

    # 時計の巻き戻り等で未来の日付が記録されていても、今日の枠は止めない
    assert len(channel.sent) == 1


def test_rotate_presence_sets_custom_activity(monkeypatch):
    client = wasabi.WasabiBot()
    change_presence = AsyncMock()
    monkeypatch.setattr(client, "change_presence", change_presence)

    asyncio.run(wasabi.rotate_presence.coro(client))

    activity = change_presence.call_args.kwargs["activity"]
    assert isinstance(activity, discord.CustomActivity)
    assert activity.name in wasabi.PRESENCE_TEXTS
    assert client.presence_text == activity.name


def test_rotate_presence_avoids_same_text_in_a_row(monkeypatch):
    client = wasabi.WasabiBot()
    client.presence_text = wasabi.PRESENCE_TEXTS[0]
    change_presence = AsyncMock()
    monkeypatch.setattr(client, "change_presence", change_presence)

    asyncio.run(wasabi.rotate_presence.coro(client))

    assert change_presence.call_args.kwargs["activity"].name != wasabi.PRESENCE_TEXTS[0]


def test_rotate_presence_allows_repeat_with_single_text(monkeypatch):
    monkeypatch.setattr(wasabi, "PRESENCE_TEXTS", ["わさび"])
    client = wasabi.WasabiBot()
    client.presence_text = "わさび"
    change_presence = AsyncMock()
    monkeypatch.setattr(client, "change_presence", change_presence)

    asyncio.run(wasabi.rotate_presence.coro(client))

    assert change_presence.call_args.kwargs["activity"].name == "わさび"


def test_rotate_presence_survives_change_presence_error(monkeypatch):
    client = wasabi.WasabiBot()
    monkeypatch.setattr(client, "change_presence", AsyncMock(side_effect=RuntimeError("boom")))

    # 例外が漏れると tasks.loop ごと止まり、以後ステータスが切り替わらなくなる
    asyncio.run(wasabi.rotate_presence.coro(client))

    assert client.presence_text is None
