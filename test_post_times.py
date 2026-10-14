from datetime import datetime

import pytest

import post_times


def test_posted_times_for_today():
    guild_config = {"last_post_date": "2026-10-14", "last_posted_times": ["00:00"]}

    assert post_times.posted_times_for_today(guild_config, "2026-10-14") == ["00:00"]
    assert post_times.posted_times_for_today(guild_config, "2026-10-15") == []


def test_parse_post_times_normalizes():
    assert post_times.parse_post_times("12:00, 9:30 00:00,12:00") == ["00:00", "09:30", "12:00"]
    assert post_times.parse_post_times("23:59、0:00") == ["00:00", "23:59"]


@pytest.mark.parametrize(
    "text",
    ["", "  ", "12:00:30", "25:00", "12:60", "12:00+09:00", "noon", "１２:００"],
)
def test_parse_post_times_rejects_invalid(text):
    with pytest.raises(ValueError):
        post_times.parse_post_times(text)


def test_parse_post_times_limits_count():
    times = [f"{hour:02d}:00" for hour in range(24)]
    assert len(post_times.parse_post_times(",".join(times))) == 24

    with pytest.raises(ValueError):
        post_times.parse_post_times(",".join([*times, "00:30"]))


def test_times_already_passed_boundaries():
    now = datetime(2026, 10, 14, 12, 46)  # noqa: DTZ001  Botはローカル時刻で判定する

    # ちょうど現在時刻は daily_post の投稿条件（scheduled <= now）とそろえて「過ぎた」側
    assert post_times.times_already_passed(["12:45", "12:46", "12:47"], now) == ["12:45", "12:46"]


def test_mark_passed_times_as_posted():
    now = datetime(2026, 10, 14, 12, 0)  # noqa: DTZ001  Botはローカル時刻で判定する
    guild_config = {"last_post_date": "2026-10-14", "last_posted_times": ["06:00"]}

    # 当日分の記録とマージし、ちょうど今の時刻も投稿済み扱いにする
    assert post_times.mark_passed_times_as_posted(guild_config, ["00:00", "12:00", "18:00"], now) == {
        "last_post_date": "2026-10-14",
        "last_posted_times": ["00:00", "06:00", "12:00"],
    }

    # 前日の記録は捨てる
    yesterday_config = {"last_post_date": "2026-10-13", "last_posted_times": ["23:00"]}
    assert post_times.mark_passed_times_as_posted(yesterday_config, ["18:00"], now) == {
        "last_post_date": "2026-10-14",
        "last_posted_times": [],
    }
