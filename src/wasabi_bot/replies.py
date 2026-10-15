import unicodedata

WASABI_REPLY = "くっだらねえ！なんだよそれ！わっかわっかしい！"
MOSIKASITE = "もしかして: [わさび](https://ja.wikipedia.org/wiki/%E3%83%AF%E3%82%B5%E3%83%93)"

# (通常版, レア版) の組。レアかどうかは自動投稿時に決まり（確率は config の rare_percent）、その後のボタンの返信もレア版になる
WASABI_POST = ("わさび", "本わさび")
EAT_REPLY = ("鼻がツーンとした！", "鼻がツーンとしなかった！")
TISSUE_REPLY = ("お前は許されないことした！:rage:", "お前は絶対に許されないことした！:rage::rage::rage:")
DIRTY_JOKE_REPLY = (
    "すまん！悪かった！:person_bowing:",
    "本当にすまん！悪かった！:person_bowing::person_bowing::person_bowing:",
)

# 前方一致で判定するので、長いキーワードを先に置く（dictは挿入順を保つ）
# キーワードと投稿はどちらも _normalize してから比較するので、全角/半角・大文字/小文字の違いは書き分け不要
KEYWORD_REPLIES = {
    "ところてん": "つるつるてん🧑‍🦲",
    "トコロテン": "ツルツルテン🧑‍🦲",
    "わらび餅": "わさび餅",
    "わさび餅": "もしかして: [わらび餅](https://www.google.com/search?q=わらび餅)",
    "わさびー": "もしかして: [ワラビー](https://www.google.com/search?q=ワラビー)",
    "わさび山": "さっさと完成させろ、このゴミカス!",
    "1あさび": MOSIKASITE,
    "2あさび": MOSIKASITE,
    "3あさび": MOSIKASITE,
    "くぁさび": MOSIKASITE,
    "えあさび": MOSIKASITE,
    "ああさび": MOSIKASITE,
    "wqさび": MOSIKASITE,
    "っwさび": MOSIKASITE,
    "wっさび": MOSIKASITE,
    "wzさび": MOSIKASITE,
    "わえあび": MOSIKASITE,
    "わああび": MOSIKASITE,
    "わsqび": MOSIKASITE,
    "わswび": MOSIKASITE,
    "わっsび": MOSIKASITE,
    "わszび": MOSIKASITE,
    "わさvい": MOSIKASITE,
    "わさb7": MOSIKASITE,
    "わさb8": MOSIKASITE,
    "わさb9": MOSIKASITE,
    "わさbj": MOSIKASITE,
    "わさbk": MOSIKASITE,
    "わさび": WASABI_REPLY,
    "ささび": MOSIKASITE,
    "わわび": MOSIKASITE,
    "わだび": MOSIKASITE,
    "わざび": MOSIKASITE,
    "わぁび": MOSIKASITE,
    "わさぎ": MOSIKASITE,
    "わさひ": MOSIKASITE,
    "わさに": MOSIKASITE,
    "わさぶ": MOSIKASITE,
    "わさぼ": MOSIKASITE,
    "からし": "もしかして: [わさび](https://www.google.com/search?q=わさび)",
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


def pick_message(messages: tuple[str, str], rare: bool) -> str:
    normal, rare_message = messages
    return rare_message if rare else normal
