from wasabi_bot import replies


def test_find_reply():
    assert replies.find_reply("わさび") == replies.WASABI_REPLY
    assert replies.find_reply("わさび食べたい") == replies.WASABI_REPLY
    assert replies.find_reply("わらび餅") == "わさび餅"  # 長いキーワードが先に一致する
    assert replies.find_reply("おいしいわさび") is None  # 前方一致なので反応しない
    assert replies.find_reply("") is None


def test_find_reply_ignores_width_and_case():
    typo = replies.MOSIKASITE
    assert replies.find_reply("wqさび") == typo
    assert replies.find_reply("ｗｑさび") == typo  # 全角
    assert replies.find_reply("WQさび") == typo  # 大文字
    assert replies.find_reply("ＷＱさび") == typo  # 全角大文字


def test_pick_message():
    messages = ("通常", "レア")
    assert replies.pick_message(messages, rare=True) == "レア"
    assert replies.pick_message(messages, rare=False) == "通常"
