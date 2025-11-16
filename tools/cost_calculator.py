#!/usr/bin/env python3

import json
import argparse
import sys
from decimal import Decimal, getcontext

# 设置 Decimal 的精度
getcontext().prec = 10

# --- Gemini 2.5 Pro 价格配置 ---
# 计价单位：每 1,000,000 (1M) tokens
TOKENS_PER_UNIT = Decimal("1000000")
# 标准层 (<= 200K) 价格
STANDARD_THRESHOLD = Decimal("200000")
STANDARD_INPUT_PRICE = Decimal("1.25")   # $1.25 / 1M input
STANDARD_OUTPUT_PRICE = Decimal("10.00")  # $10.00 / 1M output
# 大上下文层 (> 200K) 价格
LARGE_INPUT_PRICE = Decimal("2.50")     # $2.50 / 1M input
LARGE_OUTPUT_PRICE = Decimal("15.00")    # $15.00 / 1M output
# -------------------------------


def calculate_total_cost(file_path):
    """
    从 JSON 日志文件加载数据并计算总成本。
    此版本专门为 'gemini-2.5-pro' 模型设计。
    """
    total_cost = Decimal("0.0")
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 文件未找到 '{file_path}'", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"错误: 文件 '{file_path}' 不是有效的 JSON 格式。", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"读取文件时发生未知错误: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, list):
        print("错误: JSON 文件的顶层应为一个列表 (list)。", file=sys.stderr)
        sys.exit(1)

    print(f"正在处理 {len(data)} 条记录 (仅限 gemini-2.5-pro)...\n")

    for i, entry in enumerate(data):
        model = entry.get("model")
        
        # 跳过所有不是 gemini-2.5-pro 的模型
        if model != "gemini-2.5-pro" and model != "google/gemini-2.5-pro":
            if model: # 如果有模型名称，但不是 2.5 pro
                print(f"  - 提示: 第 {i+1} 条记录模型为 '{model}'，已跳过。")
            else: # 如果缺少模型字段
                print(f"  - 警告: 第 {i+1} 条记录缺少 'model' 字段，已跳过。")
            continue

        try:
            input_tokens = Decimal(entry.get("input_tokens", 0))
            output_tokens = Decimal(entry.get("output_tokens", 0))
            
            input_price = Decimal("0.0")
            output_price = Decimal("0.0")

            # --- 核心定价逻辑 ---
            if input_tokens <= STANDARD_THRESHOLD:
                # 使用标准层价格
                input_price = STANDARD_INPUT_PRICE
                output_price = STANDARD_OUTPUT_PRICE
            else:
                # 使用大上下文层价格
                input_price = LARGE_INPUT_PRICE
                output_price = LARGE_OUTPUT_PRICE
            # --------------------

            # 计算成本
            input_cost = (input_tokens / TOKENS_PER_UNIT) * input_price
            output_cost = (output_tokens / TOKENS_PER_UNIT) * output_price
            
            entry_cost = input_cost + output_cost
            
            # 累加总数
            total_cost += entry_cost
            total_input_tokens += int(input_tokens)
            total_output_tokens += int(output_tokens)
            
        except Exception as e:
            print(f"警告: 处理第 {i+1} 条记录 (模型: {model}) 时发生意外错误: {e}，已跳过。")

    return total_cost, total_input_tokens, total_output_tokens


def main():
    """
    主函数，用于解析命令行参数并调用计算。
    """
    parser = argparse.ArgumentParser(
        description="根据 API 调用日志 JSON 文件计算 'gemini-2.5-pro' 的总成本。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "json_file",
        metavar="FILE_PATH",
        type=str,
        help="包含 API 调用记录的 JSON 文件的路径"
    )

    args = parser.parse_args()

    total_cost, total_in, total_out = calculate_total_cost(args.json_file)

    print("\n--- 'gemini-2.5-pro' 统计完成 ---")
    print(f"总 Input Tokens:  {total_in:,}")
    print(f"总 Output Tokens: {total_out:,}")
    print("-------------------")
    print(f"💰 总成本: ${total_cost:,.4f} USD")


if __name__ == "__main__":
    main()