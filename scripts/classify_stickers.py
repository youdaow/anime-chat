"""按来源分类整理表情包清单，方便手动查看。"""
import json
import os
from pathlib import Path

def classify_stickers():
    main_dir = Path("data/stickers")
    quar_dir = Path("data/stickers_quarantine")
    
    def classify_files(dir_path):
        if not dir_path.exists():
            return {}
            
        files = [f for f in dir_path.iterdir() if f.is_file()]
        categories = {}
        
        for f in files:
            # 按前缀分类
            if f.name.startswith("qq收到-"):
                category = "qq收到-聊天接收"
            elif f.name.startswith("qq收藏-"):
                category = "qq收藏-个人收藏"
            elif f.name.startswith("qq超级表情-"):
                category = "qq超级表情-GIF动图"
            elif f.name.startswith("qq商店-"):
                category = "qq商店-商店表情"
            elif f.name.startswith("p") and f.name[1:3].isdigit() and f.name.endswith((".webp", ".jpg", ".png")):
                category = "p系列-可能头像相关"
            elif f.name.startswith("web-"):
                category = "web-网页下载"
            elif f.name.startswith("qq"):
                category = "qq其他-未分类"
            else:
                category = "非QQ-其他来源"
            
            if category not in categories:
                categories[category] = []
            
            size_kb = f.stat().st_size / 1024
            categories[category].append({
                "name": f.name,
                "size": round(size_kb, 1),
                "path": str(f)
            })
        
        # 按大小排序每个分类
        for cat in categories:
            categories[cat].sort(key=lambda x: x["size"], reverse=True)
            
        return categories, files
    
    # 分类主库
    main_cats, main_files = classify_files(main_dir)
    
    # 分类隔离区
    quar_cats, quar_files = classify_files(quar_dir)
    
    # 生成报告
    report = []
    report.append("# 表情包分类清单\n")
    
    # 主库
    report.append("## 📁 主库表情包 (data/stickers)")
    report.append(f"总文件数: {len(main_files)}")
    report.append("")
    
    for cat, items in main_cats.items():
        total_size = sum(item["size"] for item in items)
        report.append(f"### {cat}")
        report.append(f"数量: {len(items)} 个, 总大小: {total_size:.1f} KB")
        
        # 显示最大的5个
        report.append("最大的5个:")
        for item in items[:5]:
            report.append(f"- {item['name']} ({item['size']} KB)")
        
        if len(items) > 5:
            report.append(f"... 还有 {len(items)-5} 个文件")
        report.append("")
    
    # 隔离区
    report.append("## 🚫 隔离区表情包 (data/stickers_quarantine)")
    report.append(f"总文件数: {len(quar_files)}")
    report.append("")
    
    for cat, items in quar_cats.items():
        total_size = sum(item["size"] for item in items)
        report.append(f"### {cat}")
        report.append(f"数量: {len(items)} 个, 总大小: {total_size:.1f} KB")
        
        # 显示最大的5个
        report.append("最大的5个:")
        for item in items[:5]:
            report.append(f"- {item['name']} ({item['size']} KB)")
        
        if len(items) > 5:
            report.append(f"... 还有 {len(items)-5} 个文件")
        report.append("")
    
    # 保存报告
    with open("stickers_classification.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    
    print(f"分类清单已生成: stickers_classification.md")
    print(f"主库: {len(main_files)} 个文件")
    print(f"隔离区: {len(quar_files)} 个文件")

if __name__ == "__main__":
    classify_stickers()