from animechat.emotion import detect, label, norm


def test_positive_words_win():
    key, conf, _scores = detect("哈哈哈哈真的太好笑了")
    assert key == "happy"
    assert conf > 0.4


def test_negation_damps():
    key, _conf, _ = detect("我才不开心，一点都不开心")
    assert key != "happy"


def test_sad_and_angry():
    assert detect("我好难过，想哭了")[0] == "sad"
    assert detect("气死我了，真的火大")[0] == "angry"


def test_empty_is_neutral():
    key, conf, scores = detect("")
    assert key == "neutral" and conf == 0.0 and scores == {}


def test_norm_accepts_labels_and_keys():
    assert norm("happy") == "happy"
    assert norm("开心") == "happy"
    assert norm("傲娇") == "tsundere"
    assert norm("完全不像的东西") is None
    assert label("tsundere") == "傲娇"
