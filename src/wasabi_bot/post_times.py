import re
from datetime import datetime, timedelta
from datetime import time as datetime_time
from typing import Any

# ループの遅延（Discord API待ちやファイルI/Oでの一時的なブロッキング等）を吸収しつつ、
# 予定時刻を過ぎた状態でBotを再起動したときに即座に投稿してしまわないための猶予
POST_GRACE_PERIOD = timedelta(minutes=5)

# 1日にこれ以上投稿させないための上限（連投防止）
MAX_POST_TIMES = 24
# \d は全角数字にも一致するので [0-9] で半角に限定する
POST_TIME_PATTERN = re.compile(r"([0-9]{1,2}):([0-9]{2})")


def posted_times_for_today(guild_config: dict[str, Any], today: str) -> list[str]:
    # 日付が変わったら「今日投稿済みの時刻」はリセットする
    if guild_config.get("last_post_date") != today:
        return []
    return list(guild_config.get("last_posted_times") or [])


def is_slot_posted(guild_config: dict[str, Any], day: str, post_time: str, today: str) -> bool:
    if day == today:
        return post_time in posted_times_for_today(guild_config, today)
    # 前日の枠（日付をまたいだ猶予期間中）。今日の記録があるなら、前日の枠は投稿済みか /set_times 等で処理済み
    return guild_config.get("last_post_date") == today or post_time in posted_times_for_today(guild_config, day)


def parse_post_times(text: str) -> list[str]:
    tokens = [token for token in re.split(r"[,、\s]+", text) if token]
    if not tokens:
        raise ValueError("投稿時刻が指定されていません。")

    times: set[str] = set()
    for token in tokens:
        match = POST_TIME_PATTERN.fullmatch(token)
        if match is None or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError(f"時刻の形式が正しくありません: {token}（例: 09:30）")
        times.add(f"{int(match[1]):02d}:{match[2]}")

    if len(times) > MAX_POST_TIMES:
        raise ValueError(f"投稿時刻は{MAX_POST_TIMES}件までです。")
    return sorted(times)


def times_already_passed(times: list[str], now: datetime) -> list[str]:
    # daily_post の投稿条件（scheduled <= now）とそろえ、ちょうど今の時刻も「過ぎた」側に含める
    return [
        post_time
        for post_time in times
        if datetime.combine(now.date(), datetime_time.fromisoformat(post_time)) <= now
    ]


def mark_passed_times_as_posted(
    guild_config: dict[str, Any],
    post_times: list[str],
    now: datetime,
) -> dict[str, Any]:
    # 今日すでに過ぎた時刻は投稿済み扱いにして、設定した直後に投稿されないようにする
    today = now.date().isoformat()
    return {
        "last_post_date": today,
        "last_posted_times": sorted(
            {*posted_times_for_today(guild_config, today), *times_already_passed(post_times, now)},
        ),
    }
