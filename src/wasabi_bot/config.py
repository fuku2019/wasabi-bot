import copy
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# src/wasabi_bot/config.py から見たプロジェクトルートに置く（uv は既定で editable install するので __file__ は src 内を指す）
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"

DEFAULT_GUILD_CONFIG: dict[str, Any] = {
    "channel_id": None,
    "post_times": ["00:00", "06:00", "12:00"],
    "last_post_date": None,
    "last_posted_times": [],
}

logger = logging.getLogger("wasabi-bot")


def empty_config() -> dict[str, Any]:
    return {"guilds": {}}


def quarantine_config(label: str, reason: object) -> dict[str, Any]:
    # 読めない設定を空として扱ったまま保存すると全サーバー分が消えるので、先に退避する。
    # 退避に失敗した場合は OSError をそのまま上げ、呼び出し側に保存させない。
    backup_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{label}-{datetime.now():%Y%m%d-%H%M%S}")  # noqa: DTZ005
    CONFIG_PATH.rename(backup_path)
    logger.warning("config.json could not be used (%s). Moved it to %s.", reason, backup_path.name)
    return empty_config()


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        config = empty_config()
        try:
            save_config(config)
        except OSError as error:
            # 設定は保存されないが、起動して応答だけはできるようにする
            logger.warning("Failed to create config.json. Settings will not persist: %s", error)
        return config

    # 退避（rename）する前にファイルを閉じておく必要がある（Windowsでは開いたままだと失敗する）
    # Windows のエディタ等で BOM 付きで保存されても壊れた扱いにしないよう utf-8-sig で読む
    text = CONFIG_PATH.read_text(encoding="utf-8-sig")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as error:
        return quarantine_config("broken", error)

    if not isinstance(loaded, dict):
        return quarantine_config("broken", "not a JSON object")
    if not isinstance(loaded.get("guilds"), dict):
        # サーバーごとの設定に対応する前の形式。移行はせず、各サーバーで設定し直してもらう
        return quarantine_config("legacy", "old single-server format")

    return {"guilds": loaded["guilds"]}


def get_guild_config(config: dict[str, Any], guild_id: int | str) -> dict[str, Any]:
    stored = config["guilds"].get(str(guild_id))
    if not isinstance(stored, dict):
        stored = {}
    return copy.deepcopy({**DEFAULT_GUILD_CONFIG, **stored})


def update_guild_config(guild_id: int | str, **changes: Any) -> None:
    # load から save まで await を挟まないので、他の処理が割り込んで上書きすることはない
    config = load_config()
    config["guilds"][str(guild_id)] = {**get_guild_config(config, guild_id), **changes}
    save_config(config)


def remove_guild_config(guild_id: int | str) -> None:
    config = load_config()
    if config["guilds"].pop(str(guild_id), None) is not None:
        save_config(config)


def save_config(config: dict[str, Any]) -> None:
    temp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")

    # Windowsではエディタやウイルス対策ソフトが一瞬ファイルをロックすることがあり、
    # replace()が失敗すると投稿記録が保存されず再投稿の原因になるため短時間リトライする
    for attempt in range(5):
        try:
            temp_path.replace(CONFIG_PATH)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.2)
