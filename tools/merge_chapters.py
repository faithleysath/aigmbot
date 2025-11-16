#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import argparse
from pathlib import Path
from typing import List, Tuple


def extract_chapter_number(filename: str) -> int:
    """从文件名中提取章节编号
    
    支持格式：
    - 第1章：xxx.txt
    - 第100章：xxx.txt
    
    Args:
        filename: 文件名
        
    Returns:
        章节编号，如果无法提取则返回 -1
    """
    match = re.search(r'第(\d+)章', filename)
    if match:
        return int(match.group(1))
    return -1


def extract_chapter_title(filename: str) -> str:
    """从文件名中提取章节标题
    
    支持格式：
    - 第1章：锈与尘的序章.txt -> 第1章：锈与尘的序章
    
    Args:
        filename: 文件名
        
    Returns:
        章节标题，如果无法提取则返回原文件名（去掉.txt）
    """
    # 去掉 .txt 后缀
    title = filename.replace('.txt', '')
    return title


def get_sorted_chapter_files(input_dir: str) -> List[Tuple[int, str, str]]:
    """获取并排序章节文件
    
    Args:
        input_dir: 输入目录路径
        
    Returns:
        排序后的 (章节号, 文件路径, 章节标题) 列表
    """
    chapter_files = []
    
    # 遍历目录中的所有 .txt 文件
    for filename in os.listdir(input_dir):
        if not filename.endswith('.txt'):
            continue
            
        filepath = os.path.join(input_dir, filename)
        chapter_num = extract_chapter_number(filename)
        chapter_title = extract_chapter_title(filename)
        
        if chapter_num > 0:
            chapter_files.append((chapter_num, filepath, chapter_title))
        else:
            print(f"⚠️ 警告: 无法从 '{filename}' 中提取章节编号，已跳过。")
    
    # 按章节号排序
    chapter_files.sort(key=lambda x: x[0])
    
    return chapter_files


def merge_chapters(chapter_files: List[Tuple[int, str, str]], output_path: str, separator: str) -> None:
    """合并章节文件
    
    Args:
        chapter_files: (章节号, 文件路径, 章节标题) 列表
        output_path: 输出文件路径
        separator: 章节间分隔符
    """
    if not chapter_files:
        print("❌ 错误: 未找到任何有效的章节文件。")
        return
    
    print(f"📚 准备合并 {len(chapter_files)} 个章节...")
    print(f"📝 输出文件: {output_path}")
    print()
    
    try:
        with open(output_path, 'w', encoding='utf-8') as outfile:
            for idx, (chapter_num, filepath, chapter_title) in enumerate(chapter_files, 1):
                try:
                    # 读取章节内容
                    with open(filepath, 'r', encoding='utf-8') as infile:
                        content = infile.read().strip()
                    
                    # 写入章节标题
                    outfile.write(f"# {chapter_title}\n\n")
                    
                    # 写入章节内容
                    outfile.write(content)
                    
                    # 添加分隔符（最后一章除外）
                    if idx < len(chapter_files) and separator:
                        outfile.write(separator)
                    
                    print(f"✅ [{idx}/{len(chapter_files)}] {chapter_title}")
                    
                except Exception as e:
                    print(f"❌ 读取第{chapter_num}章时出错: {e}")
                    continue
        
        print()
        print("="*50)
        print("✨ 合并完成！")
        print(f"📄 输出文件: {output_path}")
        
        # 显示统计信息
        file_size = os.path.getsize(output_path)
        file_size_mb = file_size / (1024 * 1024)
        print(f"📊 文件大小: {file_size_mb:.2f} MB ({file_size:,} 字节)")
        print("="*50)
        
    except Exception as e:
        print(f"❌ 写入输出文件时出错: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="将文件夹中的章节文件按顺序合并为一个完整的txt文件。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "input_dir",
        help="包含章节文件的输入目录路径"
    )
    parser.add_argument(
        "-o", "--output",
        default="merged_output.txt",
        help="输出文件路径 (默认: 'merged_output.txt')"
    )
    parser.add_argument(
        "--separator",
        default="\n\n\n",
        help="章节间分隔符 (默认: 3个换行符)"
    )
    parser.add_argument(
        "--no-separator",
        action="store_true",
        help="不添加章节间分隔符"
    )
    
    args = parser.parse_args()
    
    # 验证输入目录
    if not os.path.exists(args.input_dir):
        print(f"❌ 错误: 输入目录 '{args.input_dir}' 不存在。")
        return
    
    if not os.path.isdir(args.input_dir):
        print(f"❌ 错误: '{args.input_dir}' 不是一个目录。")
        return
    
    # 处理分隔符
    separator = "" if args.no_separator else args.separator
    # 处理转义字符
    if separator:
        separator = separator.replace("\\n", "\n").replace("\\t", "\t")
    
    # 获取排序后的章节文件
    chapter_files = get_sorted_chapter_files(args.input_dir)
    
    if not chapter_files:
        print(f"❌ 错误: 在 '{args.input_dir}' 中未找到任何章节文件。")
        print("💡 提示: 确保文件名包含 '第X章' 格式的章节编号。")
        return
    
    # 确认章节范围
    first_chapter = chapter_files[0][0]
    last_chapter = chapter_files[-1][0]
    print(f"🔍 发现章节范围: 第{first_chapter}章 ~ 第{last_chapter}章")
    print()
    
    # 合并章节
    merge_chapters(chapter_files, args.output, separator)


if __name__ == "__main__":
    main()
