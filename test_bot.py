import json
from datetime import datetime

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
