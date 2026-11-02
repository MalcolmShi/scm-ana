#!/usr/bin/env python3
"""从采购入库来源到清洗明细和采购增量月报。"""
import argparse
from pathlib import Path
import subprocess
import sys
import re

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description='采购入库数据接入、清洗和月度归档')
    parser.add_argument('end_month', nargs='?', help='分析结束月 YYYY-MM；默认上月')
    args = parser.parse_args()
    if args.end_month:
        try:
            if not re.fullmatch(r'\d{4}-\d{2}', args.end_month) or str(pd.Period(args.end_month, freq='M')) != args.end_month:
                raise ValueError
        except ValueError:
            parser.error('分析结束月必须为有效的 YYYY-MM')
    root = Path(__file__).resolve().parent
    steps = [('sync_data.py', []), ('build_processed.py', []),
             ('archive_procurement_increment.py', [args.end_month] if args.end_month else [])]
    for script, arguments in steps:
        subprocess.run([sys.executable, str(root / script), *arguments], check=True)


if __name__ == '__main__':
    main()
