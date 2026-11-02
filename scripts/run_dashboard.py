#!/usr/bin/env python3
"""采购增量本地看板：查询数据库并返回表格文本。"""
from __future__ import annotations
import argparse
import csv
import io
import math
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
from services import procurement_increment as service
from paths import PROJECT_ROOT

PAGE = PROJECT_ROOT / 'src' / 'web' / 'procurement_increment.html'


def table_text(frame):
    def display(value):
        if isinstance(value, (list, tuple, np.ndarray)):
            return '、'.join(display(item) for item in value)
        if value is None or value is pd.NA or value is pd.NaT:
            return ''
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, float) and not math.isfinite(value):
            if math.isnan(value): return ''
            raise ValueError('数据包含无穷值')
        return str(value)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(frame.columns)
    for row in frame.itertuples(index=False, name=None):
        writer.writerow([display(value) for value in row])
    return stream.getvalue().encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path)
        args = {key: values[-1] for key, values in parse_qs(route.query).items()}
        headers = {}
        try:
            if route.path == '/':
                data, content = PAGE.read_bytes(), 'text/html; charset=utf-8'
            elif route.path in ('/vendor/chart.umd.min.js', '/vendor/html-to-image.js'):
                data = (PAGE.parent / route.path.lstrip('/')).read_bytes()
                content = 'text/javascript; charset=utf-8'
            else:
                if route.path == '/api/periods':
                    frame = pd.DataFrame({'month': service.available_periods()})
                elif route.path == '/api/report':
                    frame, meta = service.read_report(args['month'])
                    headers['X-Archive-Date'] = meta['archive_date']
                elif route.path == '/api/monthly':
                    frame = service.read_monthly(args['month'], args['material'])
                elif route.path == '/api/details':
                    page = int(args.get('page', '1'))
                    frame, total = service.read_details(args['month'], args['material'], purchase_month=args.get('purchase_month',''), supplier=args.get('supplier',''), company=args.get('company',''), page=page)
                    headers.update({'X-Total-Count': str(total), 'X-Page': str(page), 'X-Page-Size': '50'})
                else:
                    self.send_error(404); return
                data, content = table_text(frame), 'text/csv; charset=utf-8'
            self.send_response(200)
        except (ValueError, KeyError) as exc:
            data, content = str(exc).encode(), 'text/plain; charset=utf-8'
            self.send_response(400)
        except FileNotFoundError as exc:
            data, content = str(exc).encode(), 'text/plain; charset=utf-8'
            self.send_response(503)
        self.send_header('Content-Type', content)
        self.send_header('Content-Length', str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    port = parser.parse_args().port
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError as exc:
        parser.exit(1, f'无法启动本地服务（端口 {port}）：{exc}；可用 --port 指定其他端口。\n')
    print(f'采购增量：http://127.0.0.1:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
