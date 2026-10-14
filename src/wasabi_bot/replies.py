import unicodedata

WASABI_REPLY = "くっだらねえ！なんだよそれ！わっかわっかしい！"

# 前方一致で判定するので、長いキーワードを先に置く（dictは挿入順を保つ）
# キーワードと投稿はどちらも _normalize してから比較するので、全角/半角・大文字/小文字の違いは書き分け不要
KEYWORD_REPLIES = {
    "ところてん": "つるつるてん🧑‍🦲",
    "わらび餅": "わさび餅",
    "わさび山": "さっさと完成させろ、このゴミカス!",
    "わさび": WASABI_REPLY,
    "わだび": "もしかして: __わさび__",
    "わさに": "もしかして: __わさび__",
    "qさび": "もしかして: __わさび__",
}


def _normalize(text: str) -> str:
    # NFKC で全角英数字→半角、半角カナ→全角カナに揃え、casefold で大文字小文字を無視する
    return unicodedata.normalize("NFKC", text).casefold()


_NORMALIZED_REPLIES = [(_normalize(k), v) for k, v in KEYWORD_REPLIES.items()]


def find_reply(content: str) -> str | None:
    content = _normalize(content)
    for keyword, reply in _NORMALIZED_REPLIES:
        if content.startswith(keyword):
            return reply
    return None
