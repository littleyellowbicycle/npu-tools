#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description='NPU 工具入口',
        epilog='示例:\n'
               '  python npu_status_query.py                  # 启动飞书 WebSocket 机器人\n'
               '  python npu_status_query.py --local           # 本地模式：查询 NPU 状态\n'
               '  python npu_status_query.py --local query     # 同上\n'
               '  python npu_status_query.py --local launch train.py\n'
               '  python npu_status_query.py --local ps\n'
               '  python npu_status_query.py --local logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--local',
        action='store_true',
        default=False,
        help='本地模式：使用 CLI 管理集群',
    )
    parser.add_argument(
        'cli_args',
        nargs='*',
        help='CLI 子命令及参数（仅 --local 模式有效）',
    )
    args = parser.parse_args()

    if args.local:
        from channel.cli import main as cli_main
        sys.argv = ['npu'] + args.cli_args
        cli_main()
    else:
        from channel.feishu import main as feishu_main
        feishu_main()


if __name__ == '__main__':
    main()
