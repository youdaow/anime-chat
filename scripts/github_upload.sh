# GitHub 上传命令（复制粘贴到终端执行）

# 1. 初始化 Git 仓库（如果还没初始化）
git init

# 2. 添加所有文件（.gitignore 会自动排除不该传的）
git add .

# 3. 提交（会打开编辑器让你写说明，保存退出）
git commit -m "初始版本：角色聊天机器人"

# 4. 添加远程仓库地址
git remote add origin https://github.com/youdaow/anime-chat.git

# 5. 推送到 GitHub（第一次可能要登录 GitHub）
git push -u origin main

# 如果提示分支名不对，试试：
# git push -u origin master

# 如果提示需要身份验证，按提示输入 GitHub 用户名和密码（或 personal access token）
# 推荐用 personal access token（在 GitHub Settings > Developer settings > Personal access tokens 创建）