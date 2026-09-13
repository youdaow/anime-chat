"""跨进程稳定性：换一张图不能依赖 PYTHONHASHSEED。"""

import os
import subprocess
import sys

from animechat.stickers import library

PROBE = (
    "from animechat.stickers import library;"
    "s = library().pick_for_emotion('happy', prefs=None, seed=12345);"
    "print(s.id if s else '')"
)


def test_pick_for_emotion_same_across_hash_seeds():
    if library().pick_for_emotion("happy") is None:
        raise pytest_skip("表情库为空（还没 build-assets）")
    outs = []
    for seed in ("1", "777"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        res = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, res.stderr
        outs.append(res.stdout.strip())
    assert outs[0] == outs[1] and outs[0], outs


def pytest_skip(reason: str):
    import pytest

    return pytest.skip(reason)
