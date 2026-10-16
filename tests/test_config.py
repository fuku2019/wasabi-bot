import json

import pytest

from wasabi_bot import config


def test_load_config_creates_empty_config(config_path):
    assert config.load_config() == {"guilds": {}}
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"guilds": {}}


def test_get_guild_config_fills_missing_keys(config_path):
    config_path.write_text(json.dumps({"guilds": {"1": {"channel_id": 123}}}), encoding="utf-8")

    loaded = config.load_config()
    guild_config = config.get_guild_config(loaded, 1)

    assert guild_config["channel_id"] == 123
    assert guild_config["post_times"] == config.DEFAULT_GUILD_CONFIG["post_times"]
    assert set(guild_config) == set(config.DEFAULT_GUILD_CONFIG)
    # 未設定のサーバーにはデフォルト値を返す
    assert config.get_guild_config(loaded, 2) == config.DEFAULT_GUILD_CONFIG


def test_get_guild_config_does_not_share_default_lists():
    guild_config = config.get_guild_config({"guilds": {}}, 1)
    guild_config["post_times"].append("23:59")

    assert "23:59" not in config.DEFAULT_GUILD_CONFIG["post_times"]


@pytest.mark.parametrize("value", [0, 1, 100])
def test_rare_percent_accepts_integer_percent(value):
    assert config.rare_percent({"rare_percent": value}) == value


@pytest.mark.parametrize("value", [True, False, 0.01, 1.0, "5", -1, 101, None])
def test_rare_percent_falls_back_to_default(value):
    assert config.rare_percent({"rare_percent": value}) == config.DEFAULT_GUILD_CONFIG["rare_percent"]


def test_update_guild_config_keeps_other_guilds(config_path):
    config.update_guild_config(1, channel_id=111)
    config.update_guild_config(2, channel_id=222)
    config.update_guild_config(1, post_times=["09:00"])

    loaded = config.load_config()
    assert config.get_guild_config(loaded, 1)["channel_id"] == 111
    assert config.get_guild_config(loaded, 1)["post_times"] == ["09:00"]
    assert config.get_guild_config(loaded, 2)["channel_id"] == 222


def test_remove_guild_config(config_path):
    config.update_guild_config(1, channel_id=111)
    config.update_guild_config(2, channel_id=222)

    config.remove_guild_config(1)

    assert list(config.load_config()["guilds"]) == ["2"]


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

    assert config.load_config() == {"guilds": {}}

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
    monkeypatch.setattr(config, "save_config", saved.append)

    with pytest.raises(OSError):
        config.update_guild_config(1, channel_id=111)

    # 他サーバー分を消さないよう、退避できなければ保存しない
    assert saved == []
    assert config_path.read_text(encoding="utf-8") == "{ broken"


def test_load_config_survives_unwritable_dir(config_path, monkeypatch):
    def deny(_config):
        raise OSError("read-only")

    monkeypatch.setattr(config, "save_config", deny)

    assert config.load_config() == {"guilds": {}}


def test_load_config_accepts_utf8_bom(config_path):
    config_path.write_text(json.dumps({"guilds": {"1": {"channel_id": 123}}}), encoding="utf-8-sig")

    # Windows のエディタで保存された BOM 付きファイルを壊れた扱いで退避しない
    assert config.get_guild_config(config.load_config(), 1)["channel_id"] == 123
    assert list(config_path.parent.glob("config.json.broken-*")) == []


def test_load_config_moves_non_utf8_file_aside(config_path):
    content = '{"guilds": {"1": {"memo": "わさび"}}}'.encode("shift_jis")
    config_path.write_bytes(content)

    # Shift_JIS などで保存されたファイルも、読めないまま毎回エラーにせず退避する
    assert config.load_config() == {"guilds": {}}

    assert not config_path.exists()
    backups = list(config_path.parent.glob("config.json.broken-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == content
    # 次に読むと空の設定で作り直される
    assert config.load_config() == {"guilds": {}}
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"guilds": {}}
