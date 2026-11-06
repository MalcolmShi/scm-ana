#!/usr/bin/env python3
"""供应链分析本地看板：采购增量及采购降本模拟演示。"""
from __future__ import annotations
import argparse
import csv
import io
import math
import os
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
from services import procurement_increment as service
from services import procurement_savings_demo as savings_demo
from paths import PROJECT_ROOT

PAGE = PROJECT_ROOT / 'src' / 'web' / 'procurement_increment.html'
SAVINGS_PAGE = PROJECT_ROOT / 'src' / 'web' / 'procurement_savings.html'
STATIC_ASSETS = {
    '/vendor/chart.umd.min.js': 'text/javascript; charset=utf-8',
    '/vendor/html-to-image.js': 'text/javascript; charset=utf-8',
    '/vendor/ui-select.js': 'text/javascript; charset=utf-8',
    '/vendor/ui-select.css': 'text/css; charset=utf-8',
}


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
            elif route.path == '/savings':
                data, content = SAVINGS_PAGE.read_bytes(), 'text/html; charset=utf-8'
            elif route.path == '/api/savings-demo':
                data, content = savings_demo.read_database(), 'application/json; charset=utf-8'
            elif route.path in STATIC_ASSETS:
                data = (PAGE.parent / route.path.lstrip('/')).read_bytes()
                content = STATIC_ASSETS[route.path]
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
        except (FileNotFoundError, savings_demo.DemoDataUnavailable) as exc:
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
    # 发布到托管环境时由平台注入 PORT，并需要绑定 0.0.0.0；
    # 本机运行时仍只监听 127.0.0.1，避免把采购数据暴露到局域网。
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')))
    parser.add_argument('--host', default='0.0.0.0' if os.environ.get('PORT') else '127.0.0.1')
    args = parser.parse_args()
    port, host = args.port, args.host
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        parser.exit(1, f'无法启动本地服务（{host}:{port}）：{exc}；可用 --port 指定其他端口。\n')
    print(f'供应链分析：http://127.0.0.1:{port}  采购降本模拟：http://127.0.0.1:{port}/savings', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
