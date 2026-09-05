import json

import bot as wasabi


def test_time_pattern():
    for ok in ("00:00", "09:30", "23:59"):
        assert wasabi.TIME_PATTERN.fullmatch(ok)
    for ng in ("24:00", "9:30", "09:60", "0930", "09:30 "):
        assert not wasabi.TIME_PATTERN.fullmatch(ng)


def test_find_reply():
    assert wasabi.find_reply("わさび") == wasabi.WASABI_REPLY
    assert wasabi.find_reply("わさび食べたい") == wasabi.WASABI_REPLY
    assert wasabi.find_reply("わらび餅") == "わさび餅"  # 長いキーワードが先に一致する
    assert wasabi.find_reply("おいしいわさび") is None  # 前方一致なので反応しない
    assert wasabi.find_reply("") is None


def test_load_config_fills_missing_keys(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"channel_id": 123}), encoding="utf-8")
    monkeypatch.setattr(wasabi, "CONFIG_PATH", path)

    config = wasabi.load_config()

    assert config["channel_id"] == 123
    assert config["post_time"] is None
    assert set(config) == set(wasabi.DEFAULT_CONFIG)


def test_load_config_survives_broken_json(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(wasabi, "CONFIG_PATH", path)

    assert wasabi.load_config() == wasabi.DEFAULT_CONFIG


def test_load_config_rejects_non_object_json(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setattr(wasabi, "CONFIG_PATH", path)

    assert wasabi.load_config() == wasabi.DEFAULT_CONFIG
