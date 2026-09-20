#!/usr/bin/env python3
"""
部署脚本：把项目打包成可独立运行的版本，适合上传到服务器。

功能：
1. 收集所有依赖（包括前端）
2. 打包成 tar.gz
3. 生成简单的启动脚本
4. 输出部署说明
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist"
BUILD_DIR = DIST_DIR / "anime-chat"
ASSETS_DIR = BUILD_DIR / "assets"

# 本机专属的表情状态，和 .gitignore 里 data/stickers/qq* + data/stickers_meta.local.json
# 那两条同义（前缀表在 src/animechat/stickers.py 的 LOCAL_ONLY_PREFIXES）。
# 这里不 import animechat：构建脚本要在装好依赖之前就能跑。
LOCAL_STICKER_GLOBS = (
    "stickers_meta.local.json",
    "stickers_meta*.json.bak-*",
    "stickers_quarantine",
    "qq*.png", "qq*.jpg", "qq*.jpeg", "qq*.gif", "qq*.webp",
)

def run(cmd: list[str], cwd: Path | None = None) -> bool:
    try:
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ 执行失败: {' '.join(cmd)}")
        print(f"错误: {e.stderr}")
        return False

def clean():
    """清理之前的构建"""
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

def copy_source():
    """复制源代码（排除不需要的）"""
    print("📦 复制源代码...")
    
    # 需要保留的目录
    keep_dirs = {
        "src": "src",
        "scripts": "scripts",
        "tests": "tests",  # 可选，保留用于调试
        "README.md": "README.md",
        "pyproject.toml": "pyproject.toml",
        ".gitignore": ".gitignore",
    }
    
    for src, dst in keep_dirs.items():
        src_path = ROOT / src
        if src_path.exists():
            dst_path = BUILD_DIR / dst
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".pytest_cache", "node_modules",
                    "*.log", "*.tmp", "chrome_err.txt", "dom_dump.txt"
                ))
            else:
                shutil.copy2(src_path, dst_path)

def copy_frontend():
    """复制前端文件（如果有构建的话）"""
    print("🌐 处理前端...")
    
    # 检查是否有构建好的前端
    frontend_dir = ROOT / "src" / "animechat" / "web"
    if frontend_dir.exists():
        # 直接复制源码，服务器上会 live reload
        web_dir = BUILD_DIR / "src" / "animechat" / "web"
        if web_dir.exists():
            shutil.rmtree(web_dir)
        shutil.copytree(frontend_dir, web_dir, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".DS_Store"
        ))
        print("✅ 前端源码已复制")
    else:
        print("⚠️  没有找到前端目录，将使用纯后端模式")

def copy_assets():
    """复制资源文件"""
    print("🎨 复制资源...")
    
    # 复制角色头像和表情
    assets_src = ROOT / "src" / "animechat" / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, ASSETS_DIR)
        print("✅ 资源文件已复制")
    else:
        print("⚠️  没有找到 assets 目录")

def copy_data():
    """复制角色和表情数据（但排除敏感 + 本机专属文件）"""
    print("📋 复制角色和表情数据...")
    
    data_src = ROOT / "data"
    if not data_src.exists():
        print("⚠️  没有找到 data 目录")
        return

    # 本机专属的那半表情状态，和聊天记录/密钥一个性质，不进部署包：
    #   stickers_meta.local.json  qq* 那批图（.gitignore 也不收）的定义 + 每张被用过几次
    #   data/stickers/qq*         你私人收藏/收到的 QQ 聊天表情（几百 MB，不该上服务器）
    #   stickers_quarantine/      同步时被移出去的隔离区，是本机的回收站
    #   *.bak-*                   脚本每次批量写 meta 前存的备份
    # 漏掉这条会在服务器上把这份文件的原样盖过去 —— 那台机器的使用计数就没了。
    ignore = shutil.ignore_patterns(
        "animechat.db", "settings.json", "feishu.lock", "feishu.pid",
        *LOCAL_STICKER_GLOBS,
    )
    data_dst = BUILD_DIR / "data"
    shutil.copytree(data_src, data_dst, ignore=ignore)
    print("✅ 角色和表情数据已复制（本机 QQ 表情与其定义、使用计数已跳过）")
    print("   要让服务器也用那批表情，把 data/stickers/qq* 和 data/stickers_meta.local.json 单独 scp 过去。")

def install_deps():
    """安装依赖"""
    print("📦 安装依赖...")
    
    # 创建虚拟环境
    venv_dir = BUILD_DIR / ".venv"
    if platform.system() == "Windows":
        python_exe = "python"
        pip_exe = "python"
    else:
        python_exe = "python3"
        pip_exe = "pip3"
    
    # 创建虚拟环境
    if not run([python_exe, "-m", "venv", str(venv_dir)], cwd=BUILD_DIR):
        return False
    
    # 升级 pip
    if platform.system() == "Windows":
        pip_path = venv_dir / "Scripts" / "pip.exe"
    else:
        pip_path = venv_dir / "bin" / "pip"
    
    if not run([str(pip_path), "install", "--upgrade", "pip"], cwd=BUILD_DIR):
        return False
    
    # 安装项目依赖
    if not run([str(pip_path), "install", "-e", "."], cwd=BUILD_DIR):
        return False
    
    return True

def create_scripts():
    """创建启动脚本"""
    print("🚀 创建启动脚本...")
    
    # 启动脚本
    if platform.system() == "Windows":
        script_content = """@echo off
echo 启动 anime-chat...
echo ======================
echo.
echo 如果这是第一次运行，请先安装依赖：
echo   .venv\\Scripts\\python.exe -m pip install -e .
echo.
echo 启动服务器：
cd /d "%~dp0"
.venv\\Scripts\\python.exe -m animechat run
pause
"""
        script_path = BUILD_DIR / "start.bat"
    else:
        script_content = """#!/bin/bash
echo "启动 anime-chat..."
echo "======================"
echo ""
echo "如果这是第一次运行，请先安装依赖："
echo "   .venv/bin/python -m pip install -e ."
echo ""
echo "启动服务器："
cd "$(dirname "$0")"
.venv/bin/python -m animechat run
"""
        script_path = BUILD_DIR / "start.sh"
        script_path.chmod(0o755)
    
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    
    # README
    readme_content = """# Anime Chat - 部署版本

## 快速启动

### Windows
```cmd
start.bat
```

### Linux/Mac
```bash
./start.sh
```

## 配置

1. 首次运行前，请确保安装了依赖：
   ```bash
   .venv/bin/python -m pip install -e .
   ```

2. 访问 http://localhost:8899 打开界面

3. 配置密钥：
   - 在网页界面填写你的 AI API Key
   - 配置飞书（可选）

## 文件说明

- `src/` - 源代码
- `data/` - 角色和表情数据
- `assets/` - 静态资源
- `start.sh/bat` - 启动脚本
- `README.md` - 原始文档

## 注意事项

- 默认端口：8899
- 数据目录：./data/
- 支持热重载（修改代码后自动重启）
"""
    
    with open(BUILD_DIR / "README-DEPLOY.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

def create_tar():
    """创建 tar.gz 包"""
    print("📦 创建压缩包...")
    
    # 创建压缩包名
    system = platform.system().lower()
    arch = platform.machine().lower()
    if "64" in arch or "x86_64" in arch:
        arch = "x64"
    elif "32" in arch or "x86" in arch:
        arch = "x86"
    
    tar_name = f"anime-chat-{system}-{arch}.tar.gz"
    tar_path = DIST_DIR / tar_name
    
    # 创建压缩包
    if platform.system() == "Windows":
        # Windows 下使用 tar 命令（如果有）或者手动打包
        try:
            subprocess.run([
                "tar", "-czf", str(tar_path), "-C", str(DIST_DIR), "anime-chat"
            ], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 手动创建 zip
            import zipfile
            with zipfile.ZipFile(str(tar_path).replace('.tar.gz', '.zip'), 'w') as zipf:
                for root, dirs, files in os.walk(BUILD_DIR):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(BUILD_DIR)
                        zipf.write(file_path, arcname)
            tar_path = tar_path.with_suffix('.zip')
    else:
        subprocess.run([
            "tar", "-czf", str(tar_path), "-C", str(DIST_DIR), "anime-chat"
        ], check=True)
    
    print(f"✅ 压缩包已创建: {tar_path}")
    return tar_path

def main():
    print("🚀 开始构建部署包...")
    
    clean()
    copy_source()
    copy_frontend()
    copy_assets()
    copy_data()
    
    if not install_deps():
        print("❌ 依赖安装失败")
        return 1
    
    create_scripts()
    tar_path = create_tar()
    
    print("\n🎉 构建完成！")
    print(f"📦 部署包: {tar_path}")
    print(f"📊 大小: {tar_path.stat().st_size / 1024 / 1024:.1f} MB")
    
    print("\n📋 部署步骤:")
    print("1. 将压缩包上传到服务器")
    print("2. 解压: tar -xzf anime-chat-*.tar.gz")
    print("3. 进入目录: cd anime-chat")
    print("4. 运行启动脚本: ./start.sh (Linux/Mac) 或 start.bat (Windows)")
    print("5. 访问 http://服务器IP:8899")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())