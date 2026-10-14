WASABI_REPLY = "くっだらねえ！なんだよそれ！わっかわっかしい！"

# 前方一致で判定するので、長いキーワードを先に置く（dictは挿入順を保つ）
KEYWORD_REPLIES = {
    "ところてん": "つるつるてん🧑‍🦲",
    "わらび餅": "わさび餅",
    "わさび山": "さっさと完成させろ、このゴミカス!",
    "わさび": WASABI_REPLY,
    "わだび": "もしかして: __わさび__",
    "わさに": "もしかして: __わさび__",
    "ｑさび": "もしかして: __わさび__",
}


def find_reply(content: str) -> str | None:
    for keyword, reply in KEYWORD_REPLIES.items():
        if content.startswith(keyword):
            return reply
    return None
