import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

import bot as wasabi
from config import update_guild_config


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.sent = []

    async def _get_channel(self):
        return self

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


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

    async def fake_send(client, channel_id):
        calls.append(("send", channel_id))

    monkeypatch.setattr(wasabi, "send_wasabi_post", fake_send)

    asyncio.run(wasabi.post_now.callback(interaction))

    # 送信に3秒以上かかっても応答が失効しないよう、先に defer してから followup で返す
    assert calls == ["defer", ("send", 111)]
    interaction.followup.send.assert_awaited_once_with("投稿しました。", ephemeral=True)
    interaction.response.send_message.assert_not_awaited()


def test_post_now_reports_failure_via_followup(config_path, monkeypatch):
    update_guild_config(1, channel_id=111)
    interaction = make_post_now_interaction(wasabi.WasabiBot())

    async def fail_send(client, channel_id):
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

    asyncio.run(wasabi.send_wasabi_post(client, 111))

    # 別サーバーで押下処理中のボタンの重複チェックを壊さない
    assert channel.sent == ["わさび"]
    assert 1 in client.processed_button_messages
    assert 2 in client.button_locks
