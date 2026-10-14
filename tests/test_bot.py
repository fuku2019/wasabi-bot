import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from wasabi_bot import bot as wasabi
from wasabi_bot import replies
from wasabi_bot.config import update_guild_config


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
    client.button_locks[2] = asyncio.Lock()

    monkeypatch.setattr(replies.random, "random", lambda: 0.5)

    asyncio.run(wasabi.send_wasabi_post(client, 111))

    # 別サーバーで押下処理中のボタンの重複チェックを壊さない
    assert channel.sent == ["わさび"]
    assert 1 in client.processed_button_messages
    assert 2 in client.button_locks


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
