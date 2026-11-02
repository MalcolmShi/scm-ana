"""采购增量看板的数据查询；不计算新的业务指标。"""
from __future__ import annotations
import pandas as pd
import storage
from read_data import read_outputs_meta

SCENE = 'procurement_increment'


def _period(value: str) -> pd.Period:
    period = pd.Period(value, freq='M')
    if str(period) != value:
        raise ValueError('月份必须为 YYYY-MM')
    return period


def available_periods() -> list[str]:
    with storage.connect(read_only=True) as con:
        return [str(day)[:7] for (day,) in con.execute('SELECT business_end FROM meta.output_periods WHERE scene=? ORDER BY business_end DESC', [SCENE]).fetchall()]


def read_report(month: str):
    end = str(_period(month).end_time.date())
    return read_outputs_meta(SCENE, end)


def read_monthly(month: str, material: str):
    end = str(_period(month).end_time.date())
    read_outputs_meta(SCENE, end)  # 先校验输入未变
    frame, _ = read_outputs_meta(SCENE, end, 'monthly')
    return frame.loc[frame['物料编码'].astype(str).eq(material)].sort_values('采购月').reset_index(drop=True)


def read_details(month: str, material: str, *, purchase_month: str = '', supplier: str = '', company: str = '', page: int = 1, size: int = 50):
    period = _period(month)
    end = str(period.end_time.date())
    if page < 1 or not 1 <= size <= 200:
        raise ValueError('分页参数无效')
    low, high = (period - 11).start_time.date(), period.end_time.date()
    if purchase_month:
        selected = _period(purchase_month)
        if not period - 11 <= selected <= period:
            raise ValueError('采购月份不在分析窗口内')
        low, high = selected.start_time.date(), selected.end_time.date()
    where = '''_business_day BETWEEN ? AND ? AND CAST(物料编码 AS VARCHAR)=?
        AND 板块 IN ('检验','检验产品','诊断') AND 供应商类型='外部单位' '''
    args = [low, high, material]
    if supplier:
        where += ' AND 供应商名称=?'; args.append(supplier)
    if company:
        where += ' AND 公司名称=?'; args.append(company)
    with storage.connect(read_only=True) as con:
        con.begin()
        storage.output_metadata(con, SCENE, end)
        exists = con.execute('SELECT count(*) FROM outputs.procurement_increment__detail WHERE _business_end=? AND CAST(物料编码 AS VARCHAR)=?', [end, material]).fetchall()[0][0]
        if not exists:
            raise ValueError('物料不在本期报表中')
        total = con.execute(f'SELECT count(*) FROM processed.procurement_inbound WHERE {where}', args).fetchone()[0]
        frame = con.execute(f'SELECT * EXCLUDE (_business_day) FROM processed.procurement_inbound WHERE {where} ORDER BY _business_day, rowid LIMIT ? OFFSET ?', args + [size, (page-1)*size]).fetchdf()
        con.commit()
    first = ['出入库日期','物料编码','物料名称','规格','主单位','供应商名称','公司名称','数量','采购单价','采购金额(万)']
    return frame[first + [c for c in frame if c not in first]], total
