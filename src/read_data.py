"""显式读取数据：每个入口只读取名称对应的数据层。"""
from __future__ import annotations
from typing import Optional
import pandas as pd
import storage

DATASET = 'procurement_inbound'


def resolve_window(start: str = '', end: str = '') -> tuple[str, str]:
    def bound(value, last):
        try:
            if len(value) == 4 and value.isdigit():
                value += '-12' if last else '-01'
            if len(value) == 7:
                period = pd.Period(value, freq='M')
                if str(period) != value:
                    raise ValueError
                return str((period.end_time if last else period.start_time).date())
            day = pd.Timestamp(value).date().isoformat()
            if day != value:
                raise ValueError
            return day
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f'日期应为 YYYY、YYYY-MM 或 YYYY-MM-DD：{value}') from exc
    if not start and not end:
        return '0001-01-01', '9999-12-31'
    low = bound(start, False) if start else '0001-01-01'
    high = bound(end, True) if end else bound(start, True)
    if low > high:
        raise ValueError('开始日期不能晚于结束日期')
    return low, high


def _read(layer: str, dataset: str, start='', end='', columns=None, *, source_dataset=None):
    low, high = resolve_window(start, end)
    frame, parts, raw_parts = storage.read_window(layer, dataset, low, high, columns, source_dataset=source_dataset)
    coverage = {'business_coverage': '未核实'}
    if layer == 'processed' and source_dataset is not None:
        raw = {p['day']: p for p in raw_parts}
        processed = {p['day']: p for p in parts}
        missing = sorted(set(raw) - set(processed))
        stale = sorted(day for day in raw.keys() & processed.keys()
                       if processed[day]['source_fingerprint'] is not None
                       and (processed[day]['source_dataset'] != source_dataset
                            or processed[day]['source_fingerprint'] != raw[day]['fingerprint']))
        if missing or stale:
            raise ValueError(f'processed 缺少日档：{missing}；已过期：{stale}')
        coverage.update(raw_days=sorted(raw), processed_days=sorted(processed),
                        missing_raw_days=sorted(processed.keys() - raw.keys()),
                        input_versions='未核实' if not parts or any(p['source_fingerprint'] is None for p in parts) else '已核实')
    return frame, {'layer': layer, 'dataset': dataset, 'window_start': low, 'window_end': high,
                   'days': [p['day'] for p in parts], 'partitions': parts, 'rows': len(frame), 'coverage': coverage}


def read_raw_procurement_inbound(start='', end='', columns=None):
    return _read('raw', DATASET, start, end, columns)[0]


def read_raw_procurement_inbound_meta(start='', end='', columns=None):
    return _read('raw', DATASET, start, end, columns)


def read_processed_procurement_inbound(start='', end='', columns=None):
    return _read('processed', DATASET, start, end, columns, source_dataset=DATASET)[0]


def read_processed_procurement_inbound_meta(start='', end='', columns=None):
    return _read('processed', DATASET, start, end, columns, source_dataset=DATASET)


def read_outputs(scene: str, business_end: str, table='detail'):
    return read_outputs_meta(scene, business_end, table)[0]


def read_outputs_meta(scene: str, business_end: str, table='detail'):
    return storage.read_outputs(scene, business_end, table)
