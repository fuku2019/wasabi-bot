from wasabi_bot import replies


def test_find_reply():
    assert replies.find_reply("わさび") == replies.WASABI_REPLY
    assert replies.find_reply("わさび食べたい") == replies.WASABI_REPLY
    assert replies.find_reply("わらび餅") == "わさび餅"  # 長いキーワードが先に一致する
    assert replies.find_reply("おいしいわさび") is None  # 前方一致なので反応しない
    assert replies.find_reply("") is None


def test_find_reply_ignores_width_and_case():
    typo = "もしかして: __わさび__"
    assert replies.find_reply("qさび") == typo
    assert replies.find_reply("ｑさび") == typo  # 全角
    assert replies.find_reply("Qさび") == typo  # 大文字
    assert replies.find_reply("Ｑさび") == typo  # 全角大文字


def test_roll_rare(monkeypatch):
    monkeypatch.setattr(replies.random, "random", lambda: 0.0)
    assert replies.roll_rare()
    monkeypatch.setattr(replies.random, "random", lambda: replies.RARE_CHANCE)
    assert not replies.roll_rare()  # 境界値はレアにならない
    monkeypatch.setattr(replies.random, "random", lambda: 0.99)
    assert not replies.roll_rare()


def test_pick_message():
    messages = ("通常", "レア")
    assert replies.pick_message(messages, rare=True) == "レア"
    assert replies.pick_message(messages, rare=False) == "通常"
