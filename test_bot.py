import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot as wasabi


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(wasabi, "CONFIG_PATH", path)
    return path


def test_find_reply():
    assert wasabi.find_reply("わさび") == wasabi.WASABI_REPLY
    assert wasabi.find_reply("わさび食べたい") == wasabi.WASABI_REPLY
    assert wasabi.find_reply("わらび餅") == "わさび餅"  # 長いキーワードが先に一致する
    assert wasabi.find_reply("おいしいわさび") is None  # 前方一致なので反応しない
    assert wasabi.find_reply("") is None


def test_load_config_creates_empty_config(config_path):
    assert wasabi.load_config() == {"guilds": {}}
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"guilds": {}}


def test_get_guild_config_fills_missing_keys(config_path):
    config_path.write_text(json.dumps({"guilds": {"1": {"channel_id": 123}}}), encoding="utf-8")

    config = wasabi.load_config()
    guild_config = wasabi.get_guild_config(config, 1)

    assert guild_config["channel_id"] == 123
    assert guild_config["post_times"] == wasabi.DEFAULT_GUILD_CONFIG["post_times"]
    assert set(guild_config) == set(wasabi.DEFAULT_GUILD_CONFIG)
    # 未設定のサーバーにはデフォルト値を返す
    assert wasabi.get_guild_config(config, 2) == wasabi.DEFAULT_GUILD_CONFIG


def test_get_guild_config_does_not_share_default_lists():
    guild_config = wasabi.get_guild_config({"guilds": {}}, 1)
    guild_config["post_times"].append("23:59")

    assert "23:59" not in wasabi.DEFAULT_GUILD_CONFIG["post_times"]


def test_update_guild_config_keeps_other_guilds(config_path):
    wasabi.update_guild_config(1, channel_id=111)
    wasabi.update_guild_config(2, channel_id=222)
    wasabi.update_guild_config(1, post_times=["09:00"])

    config = wasabi.load_config()
    assert wasabi.get_guild_config(config, 1)["channel_id"] == 111
    assert wasabi.get_guild_config(config, 1)["post_times"] == ["09:00"]
    assert wasabi.get_guild_config(config, 2)["channel_id"] == 222


def test_remove_guild_config(config_path):
    wasabi.update_guild_config(1, channel_id=111)
    wasabi.update_guild_config(2, channel_id=222)

    wasabi.remove_guild_config(1)

    assert list(wasabi.load_config()["guilds"]) == ["2"]


@pytest.mark.parametrize(
    ("content", "label"),
    [
        ("{ broken", "broken"),
        ("[1, 2]", "broken"),
        (json.dumps({"channel_id": 123, "post_times": ["00:00"]}), "legacy"),
    ],
)
def test_load_config_moves_unusable_file_aside(config_path, content, label):
    config_path.write_text(content, encoding="utf-8")

    assert wasabi.load_config() == {"guilds": {}}

    assert not config_path.exists()
    backups = list(config_path.parent.glob(f"config.json.{label}-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content


def test_load_config_raises_when_backup_fails(config_path, monkeypatch):
    config_path.write_text("{ broken", encoding="utf-8")
    saved = []

    def fail_rename(self, target):
        raise OSError("locked")

    monkeypatch.setattr(type(config_path), "rename", fail_rename)
    monkeypatch.setattr(wasabi, "save_config", saved.append)

    with pytest.raises(OSError):
        wasabi.update_guild_config(1, channel_id=111)

    # 他サーバー分を消さないよう、退避できなければ保存しない
    assert saved == []
    assert config_path.read_text(encoding="utf-8") == "{ broken"


def test_load_config_survives_unwritable_dir(config_path, monkeypatch):
    def deny(config):
        raise OSError("read-only")

    monkeypatch.setattr(wasabi, "save_config", deny)

    assert wasabi.load_config() == {"guilds": {}}


def test_posted_times_for_today():
    guild_config = {"last_post_date": "2026-10-14", "last_posted_times": ["00:00"]}

    assert wasabi.posted_times_for_today(guild_config, "2026-10-14") == ["00:00"]
    assert wasabi.posted_times_for_today(guild_config, "2026-10-15") == []


def test_parse_post_times_normalizes():
    assert wasabi.parse_post_times("12:00, 9:30 00:00,12:00") == ["00:00", "09:30", "12:00"]
    assert wasabi.parse_post_times("23:59、0:00") == ["00:00", "23:59"]


@pytest.mark.parametrize(
    "text",
    ["", "  ", "12:00:30", "25:00", "12:60", "12:00+09:00", "noon", "１２:００"],
)
def test_parse_post_times_rejects_invalid(text):
    with pytest.raises(ValueError):
        wasabi.parse_post_times(text)


def test_parse_post_times_limits_count():
    times = [f"{hour:02d}:00" for hour in range(24)]
    assert len(wasabi.parse_post_times(",".join(times))) == 24

    with pytest.raises(ValueError):
        wasabi.parse_post_times(",".join([*times, "00:30"]))


def test_times_already_passed_boundaries():
    now = datetime(2026, 10, 14, 12, 46)  # noqa: DTZ001  Botはローカル時刻で判定する

    # ちょうど現在時刻は daily_post の投稿条件（scheduled <= now）とそろえて「過ぎた」側
    assert wasabi.times_already_passed(["12:45", "12:46", "12:47"], now) == ["12:45", "12:46"]


def test_mark_passed_times_as_posted():
    now = datetime(2026, 10, 14, 12, 0)  # noqa: DTZ001  Botはローカル時刻で判定する
    guild_config = {"last_post_date": "2026-10-14", "last_posted_times": ["06:00"]}

    # 当日分の記録とマージし、ちょうど今の時刻も投稿済み扱いにする
    assert wasabi.mark_passed_times_as_posted(guild_config, ["00:00", "12:00", "18:00"], now) == {
        "last_post_date": "2026-10-14",
        "last_posted_times": ["00:00", "06:00", "12:00"],
    }

    # 前日の記録は捨てる
    yesterday_config = {"last_post_date": "2026-10-13", "last_posted_times": ["23:00"]}
    assert wasabi.mark_passed_times_as_posted(yesterday_config, ["18:00"], now) == {
        "last_post_date": "2026-10-14",
        "last_posted_times": [],
    }


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
    wasabi.update_guild_config(1, channel_id=111)
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
    wasabi.update_guild_config(1, channel_id=111)
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
