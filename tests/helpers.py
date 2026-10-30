"""测试共用的小工具：临时目录与一行样例记录。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from paths import PROJECT_ROOT

#: 临时目录放在工作区内，不依赖系统临时目录
TEST_TMP = PROJECT_ROOT / ".test-tmp"


def make_temp_dir(testcase: unittest.TestCase) -> Path:
    """建一个工作区内的临时目录，测试结束自动清理。"""
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=TEST_TMP))
    testcase.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def inbound_row(**changes):
    """一行入库明细导出记录的原始字段，列名与上游 Excel 一致。"""
    return {
        "分析类型": "入库",
        "年份": 2026,
        "月份": 9,
        "板块": "检验",
        "公司编码": "3004",
        "公司名称": "浙江迪安医疗器械有限公司",
        "仓库名称": "合格品仓",
        "出入库类型名称": "PO接收",
        "出入库日期": "2026-09-30",
        "物料编码": "00123",
        "物料名称": "重碳酸盐检测试剂盒",
        "规格": "250测试",
        "主单位": "盒",
        "数量": 12,
        "采购单价": 141.62,
        "采购金额(万)": 0.169944,
        "税率": 0.13,
        "采购不含税单价": 125.327434,
        "采购不含税金额(万)": 0.15039292,
        "供应商名称": "罗氏诊断产品公司",
        "一级分类": "试剂",
        "extra_field": "保留未知字段",
        **changes,
    }
