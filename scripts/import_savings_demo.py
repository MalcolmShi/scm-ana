#!/usr/bin/env python3
"""导入采购降本模拟数据；重跑将替换演示库中的模拟数据。"""
from services import procurement_savings_demo as demo


def main():
    counts = demo.import_demo_database()
    print(f'模拟数据库：{demo.DATABASE}')
    for table, count in counts.items():
        print(f'{demo.SCHEMA}.{table}: {count} 行')


if __name__ == '__main__':
    main()
