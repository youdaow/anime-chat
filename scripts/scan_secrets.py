"""上传前的密钥 / 隐私扫描（只读，不改任何文件）。

两道检查，缺一不可：

  1. 已知秘密精确匹配：把 data/settings.json 里所有 SECRET_FIELDS 的**真实值**
     收集起来，在任何会被提交的文件里做子串匹配。不依赖形态猜测，最可靠 ——
     第一版我写了「32 位小写 hex」想抓飞书 secret，实测**完全漏报**（真值是
     gsXK… 大小写混合），所以别再靠正则猜格式。
  2. 通用正则兜底：抓没存进 settings 但写在代码/注释里的硬编码 Key。

另外列出「会被提交的可疑文件」，方便确认没有整目录漏进 .gitignore。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 会被提交的文件里不该出现的东西
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OpenAI 风格 Key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub PAT(新)", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{24,}")),
    ("AWS AKIA", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("私钥文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Basic 认证头", re.compile(r"Authorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{16,}")),
    ("URL 内嵌密码", re.compile(r"://[^\s/@:]{2,}:[^\s/@]{2,}@")),
    ("飞书 App ID", re.compile(r"\bcli_[0-9a-f]{16}\b")),
    # JSON / ini 形式的键值：键名带引号时前面是 _ 而不是词边界，所以 \b 会漏
    ("键值对赋值", re.compile(
        r"""(?i)["']?[\w-]*(api[_-]?key|secret|token|passwd|password)[\w-]*["']?\s*[=:]\s*["'][^"'\s]{12,}["']""")),
    # 裸的 32 位大小写混合随机串（飞书 App Secret 的真实形态）
    ("32位混合随机串", re.compile(r"\b(?=[0-9A-Za-z]{32}\b)(?=[^\s]*[a-z])(?=[^\s]*[A-Z])(?=[^\s]*\d)[0-9A-Za-z]{32}\b")),
]

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", "dist"}
# 明确会被 .gitignore 排除的路径：扫到只提示，不算泄漏
WOULD_IGNORE = ("data/", "pack-export/", ".env")
# 测试里合法的假 Key
FAKE_OK = re.compile(r"(super-|fake|dummy|example|placeholder|test|xxxx)", re.I)


def known_secrets() -> dict[str, str]:
    """settings.json 里的真凭据，按字段名收下来做精确匹配。"""
    p = ROOT / "data" / "settings.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for key in ("llm_api_key", "feishu_app_secret", "github_token",
                "tenor_api_key", "giphy_api_key"):
        val = str(data.get(key) or "").strip()
        if len(val) >= 8:
            out[key] = val
    return out


def is_ignored(rel: str) -> bool:
    return rel.startswith(WOULD_IGNORE)


def scan() -> tuple[int, int]:
    secrets = known_secrets()
    print(f"已收录 {len(secrets)} 个真实凭据做精确匹配：" + ", ".join(secrets))
    print("-" * 68)
    leaks = 0
    warns = 0
    for path in sorted(ROOT.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        ignored = is_ignored(rel)
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > 3_000_000 or b"\x00" in data[:4096]:
            continue
        text = data.decode("utf-8", "ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            found: list[tuple[str, bool]] = []
            for name, val in secrets.items():
                if val in line:
                    # 真值命中：任何位置都是硬泄漏，包括 tests/ —— 不小心把真 Key
                    # 贴进测试用例是真实发生过的事故，绝不降级。
                    found.append((f"真值命中 {name}", True))
            if not ignored:
                in_tests = rel.startswith("tests/") or rel.endswith(".example")
                for name, pat in PATTERNS:
                    m = pat.search(line)
                    if m and not FAKE_OK.search(m.group(0)):
                        found.append((f"{name}: {m.group(0)[:14]}…", not in_tests))
            for item, hard in found:
                tag = "已忽略" if ignored else ("⚠ 会被提交" if hard else "提示(测试)")
                print(f"[{tag}] {rel}:{line_no}  {item}")
                if ignored:
                    warns += 1
                elif hard:
                    leaks += 1
    return leaks, warns


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    leaks, warns = scan()
    print("-" * 68)
    print(f"会被提交的泄漏 {leaks} 处；已忽略目录内 {warns} 处（正常）")
    sys.exit(1 if leaks else 0)
