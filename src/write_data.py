"""显式写入数据：按业务日写 raw/processed，按业务期写 outputs。"""
from __future__ import annotations
import pandas as pd
import storage

DATASET = 'procurement_inbound'


def _validate_day(day, frame):
    if '出入库日期' not in frame:
        raise ValueError('缺少出入库日期')
    dates = pd.to_datetime(frame['出入库日期'], errors='coerce').dt.strftime('%Y-%m-%d')
    if dates.isna().any() or not dates.eq(day).all():
        raise ValueError(f'记录的出入库日期与业务日不一致：{day}')


def write_raw_procurement_inbound(day, frame):
    _validate_day(day, frame)
    storage.replace_partition('raw', DATASET, day, frame)


def write_processed_procurement_inbound(day, frame, *, raw_fingerprint):
    _validate_day(day, frame)
    storage.replace_partition('processed', DATASET, day, frame,
                              source_dataset=DATASET, source_fingerprint=raw_fingerprint)


def write_outputs(scene, business_end, tables, metadata):
    storage.replace_outputs(scene, business_end, tables, metadata)


def write_raw_procurement_inbound_days(frames):
    for day, frame in frames.items():
        _validate_day(day, frame)
    storage.replace_partitions('raw', DATASET, frames)


def write_processed_procurement_inbound_days(frames, *, raw_fingerprints):
    for day, frame in frames.items():
        _validate_day(day, frame)
    storage.replace_partitions('processed', DATASET, frames,
                               source_dataset=DATASET, source_fingerprints=raw_fingerprints)
