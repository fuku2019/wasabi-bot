from wasabi_bot import replies


def test_find_reply():
    assert replies.find_reply("わさび") == replies.WASABI_REPLY
    assert replies.find_reply("わさび食べたい") == replies.WASABI_REPLY
    assert replies.find_reply("わらび餅") == "わさび餅"  # 長いキーワードが先に一致する
    assert replies.find_reply("おいしいわさび") is None  # 前方一致なので反応しない
    assert replies.find_reply("") is None
