"""测试跑在临时数据目录里，绝不碰你真正的 data/。"""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="animechat-test-")
os.environ["ANIMECHAT_DATA_DIR"] = _tmp
os.environ["ANIMECHAT_LLM_API_KEY"] = ""
os.environ.pop("ANIMECHAT_LLM_BASE_URL", None)
# AI 生成角色现在会先联网找头像（websearch），这条路要用假搜图单独测
# （test_avatar_web.py）；在这里留一道 env 开关，是为了让别的测试文件不会
# 因为一次 AI 生成悄悄往外发请求——那会让测试变慢、变飘，还可能落一张真图。
os.environ["ANIMECHAT_AVATAR_FROM_WEB"] = "0"

import pytest  # noqa: E402


def _move_avatars_out_of_the_tree() -> None:
    """AVATAR_DIR 历史上指向包里的 assets/avatars，于是测试里每画一张、
       每联网存一张头像，都真往源码树里写 PNG。挪进临时目录，跑完就没了。
       （media.avatar_path 自己会 mkdir，被 _fresh_data 清掉也能自愈。）"""
    from pathlib import Path

    import animechat.media as media_mod

    media_mod.AVATAR_DIR = Path(_tmp) / "avatars"
    media_mod.AVATAR_DIR.mkdir(parents=True, exist_ok=True)


_move_avatars_out_of_the_tree()


@pytest.fixture(autouse=True)
def _fresh_data():
    """每个测试干净的库：清文件 + 重建 store/book/library 单例。"""
    from animechat import config
    from animechat.characters import CharacterBook
    from animechat.store import Store

    import shutil
    for name in os.listdir(config.DATA_DIR):
        path = os.path.join(config.DATA_DIR, name)
        if os.path.isfile(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    import animechat.store as store_mod
    import animechat.characters as characters_mod
    import animechat.stickers as stickers_mod

    store_mod._store = Store()
    characters_mod._book = CharacterBook()
    stickers_mod._lib = None
    yield
