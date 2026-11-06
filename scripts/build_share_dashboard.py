#!/usr/bin/env python3
"""生成可分享的看板：一个页面装两个场景，样式与本地服务一致。

用法：
  uv run python scripts/build_share_dashboard.py
  uv run python scripts/build_share_dashboard.py --out outputs/share

做法：
  - 两个场景各自整份复用 src/web 下的页面，样式、结构、图表都不改；
  - 场景导航改成页内切换，两个场景文档内嵌在同一个文件里；
  - /api/* 的 fetch 换成内联数据，页面可离线打开；
  - 数据按场景分目录导出：采购增量按月，采购降本按模拟库真实表。

输出：
  outputs/share/供应链分析.html            单文件页面（两个场景）
  outputs/share/采购增量/采购增量-YYYY-MM.csv
  outputs/share/采购降本/<表名>.csv
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import duckdb
import storage
from paths import OUTPUTS_ROOT, PROJECT_ROOT, TIMEZONE
from read_data import read_outputs
from services import procurement_savings_demo
from services.procurement_increment import read_report

SCENE = 'procurement_increment'
DROP_COLUMNS = ('业务结束月', '_business_end')
SOURCE_PAGE = PROJECT_ROOT / 'src' / 'web' / 'procurement_increment.html'
SAVINGS_PAGE = PROJECT_ROOT / 'src' / 'web' / 'procurement_savings.html'
VENDOR = PROJECT_ROOT / 'src' / 'web' / 'vendor'

# 分享版是一个文件里装两个场景，靠页内切换，和本地服务的导航一致。
# 两个场景文档内嵌在 <script type="text/html"> 里，令牌用来藏住文档内的 </script。
DOC_TOKEN = '@@SCENE_SCRIPT_END@@'

SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>供应链分析</title>
<style>
  html, body { margin: 0; height: 100%; background: #f7f5f1; }
  #scene { display: block; width: 100%; height: 100%; border: 0; }
</style>
</head>
<body>
<iframe id="scene" title="供应链分析"></iframe>
<script type="text/html" id="doc-increment">@@INCREMENT@@</script>
<script type="text/html" id="doc-savings">@@SAVINGS@@</script>
<script>
(function () {
  var raw = {
    increment: document.getElementById('doc-increment').textContent,
    savings: document.getElementById('doc-savings').textContent
  };
  var docs = {};
  for (var key in raw) { docs[key] = raw[key].split('@@TOKEN@@').join('<\\/script'); }
  var frame = document.getElementById('scene');
  function showView(name) {
    if (!docs[name]) { name = 'increment'; }
    frame.srcdoc = docs[name];
    try { history.replaceState(null, '', '#' + name); } catch (err) {}
  }
  window.showView = showView;
  showView((location.hash || '').replace('#', '') || 'increment');
})();
</script>
</body>
</html>"""

SHIM = """var SHARE_META=null;
function shareMeta(){if(!SHARE_META)SHARE_META=JSON.parse(document.getElementById('share-meta').textContent);return SHARE_META}
var SHARE_REPORTS={};
function shareReport(month){if(!SHARE_REPORTS[month]){var el=document.getElementById('share-report-'+month);if(!el)return null;SHARE_REPORTS[month]=JSON.parse(el.textContent)}return SHARE_REPORTS[month]}
var SHARE_MONTHLY=null;
function shareMonthly(){if(!SHARE_MONTHLY)SHARE_MONTHLY=JSON.parse(document.getElementById('share-monthly').textContent);return SHARE_MONTHLY}
async function api(path,params){
  params=params||{};
  if(path==='/api/periods')return shareMeta().periods.slice();
  if(path==='/api/report'){
    var m=shareReport(params.month);if(!m)throw Error('该月份没有归档数据');
    if(!m._rows){m._rows=m.rows.map(function(values){var o={};m.columns.forEach(function(c,i){o[c]=values[i]});return o})}
    return {data:{columns:m.columns,rows:m._rows},archiveDate:m.archive_date};
  }
  if(path==='/api/monthly'){
    var key=String(params.material),series=(shareMonthly()[key]||[]).filter(function(x){return x[0]<=params.month});
    return {columns:['采购月','月采购量','月含税金额万'],rows:series.map(function(x){return {'采购月':x[0],'月采购量':x[1],'月含税金额万':x[2]}})};
  }
  if(path==='/api/details')return {data:{columns:[],rows:[]},total:0,pageSize:50};
  throw Error('未知接口：'+path);
}"""


def collect_periods() -> list[str]:
    with storage.connect(read_only=True) as con:
        rows = con.execute(
            'SELECT business_end FROM meta.output_periods WHERE scene=? ORDER BY business_end DESC',
            [SCENE]).fetchall()
    return [str(day) for (day,) in rows]


def build_payload(ends: list[str]) -> tuple[dict, dict[str, object]]:
    reports: dict[str, dict] = {}
    frames: dict[str, object] = {}
    for end in ends:
        frame, meta = read_report(end[:7])
        keep = [column for column in frame.columns if column not in DROP_COLUMNS]
        frame = frame.loc[:, keep]
        frames[end] = frame
        reports[end[:7]] = {
            'columns': keep,
            'rows': json.loads(frame.to_json(orient='values', force_ascii=False)),
            'archive_date': str(meta.get('archive_date', '')),
        }

    monthly: dict[str, list] = {}
    latest = ends[0]
    mframe = read_outputs(SCENE, latest, 'monthly')
    mframe = mframe.loc[:, [c for c in mframe.columns if c not in DROP_COLUMNS]]
    for row in json.loads(mframe.to_json(orient='values', force_ascii=False)):
        monthly.setdefault(str(row[0]), []).append(row[1:])

    payload = {
        'generated_at': datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M'),
        'periods': [end[:7] for end in ends],
        'reports': reports,
        'monthly': monthly,
        'monthly_snapshot': latest,
    }
    return payload, frames


def inline_assets(html: str) -> str:
    for name in ('chart.umd.min.js', 'html-to-image.js', 'ui-select.js'):
        code = (VENDOR / name).read_text(encoding='utf-8')
        html = html.replace(f'<script src="/vendor/{name}"></script>',
                            '<script>\n' + code + '\n</script>')
    css = (VENDOR / 'ui-select.css').read_text(encoding='utf-8')
    html = html.replace('<link rel="stylesheet" href="/vendor/ui-select.css">',
                        '<style>\n' + css + '\n</style>')
    return html


def _replace_once(html: str, old: str, new: str, label: str) -> str:
    """按锚点替换一次；锚点消失说明源页面改过，直接报错而不是静默漂移。"""
    hit = html.count(old)
    if hit != 1:
        raise SystemExit(
            f'分享版补丁失效（{label}）：锚点在源页面出现 {hit} 次，预期 1 次。\n'
            f'源页面 src/web/procurement_increment.html 已改动，请同步更新本脚本的锚点。')
    return html.replace(old, new, 1)


def style_block(html: str) -> str:
    start = html.index('<style>')
    return html[start:html.index('</style>', start) + len('</style>')]


def _replace_nav(html: str, current: str) -> str:
    """两页共用同一套场景导航；分享版改成页内切换，不再跳转到另一个文件。"""
    def link(target: str, label: str, active: bool) -> str:
        attr = ' aria-current="page"' if active else ''
        return (f'<a href="#" onclick="parent.showView(\'{target}\');return false"{attr}>'
                f'{label}</a>')

    nav = ('<nav class="suite-nav" aria-label="分析场景">'
           + link('increment', '采购增量', current == 'increment')
           + link('savings', '采购降本 <span style="font-size:11px">· 模拟</span>', current == 'savings')
           + '</nav>')
    pattern = re.compile(r'<nav class="suite-nav"[\s\S]*?</nav>')
    if len(pattern.findall(html)) != 1:
        raise SystemExit('分享版补丁失效（场景导航）：源页面中 suite-nav 不唯一，请检查锚点。')
    return pattern.sub(nav, html, count=1)


def inject_share_data(html: str, payload_json: str) -> str:
    body = payload_json.replace('</', r'<\/')
    tag = f'<script id="share-data" type="application/json">{body}</script>\n'
    marker = html.index('<script>', html.index('<body>'))
    return html[:marker] + tag + html[marker:]


def inject_increment_blocks(html: str, payload: dict) -> str:
    """增量页的数据按块拆开：概览、每月报表、月度趋势各一块，用到才解析。"""
    def block(block_id: str, body) -> str:
        text = json.dumps(body, ensure_ascii=False, separators=(',', ':')).replace('</', r'<\/')
        return f'<script id="{block_id}" type="application/json">{text}</script>\n'

    parts = [block('share-meta', {'periods': payload['periods'],
                                  'monthly_snapshot': payload['monthly_snapshot']})]
    parts += [block(f'share-report-{month}', data) for month, data in payload['reports'].items()]
    parts.append(block('share-monthly', payload['monthly']))

    marker = html.index('<script>', html.index('<body>'))
    return html[:marker] + ''.join(parts) + html[marker:]


def patch_page(html: str, payload: dict) -> str:
    source_style = style_block(html)

    html = inline_assets(html)

    if html.count('async function api(path,params){') != 1:
        raise SystemExit('分享版补丁失效（api 函数）：源页面中找不到唯一的 api 定义。')
    try:
        start = html.index('async function api(path,params){')
        end = html.index('  return data;\n}', start) + len('  return data;\n}')
    except ValueError as exc:
        raise SystemExit(f'分享版补丁失效（api 函数结尾）：{exc}。请同步更新锚点。') from exc
    html = html[:start] + SHIM + html[end:]

    html = _replace_once(
        html,
        "state.total=res.total;el('detailInfo').textContent=(month||'全部月份')+' · '+res.total.toLocaleString()+' 条记录';",
        "state.total=res.total;el('detailInfo').textContent='分享版不包含逐笔入库明细，需明细请在本机看板查询';",
        '明细条数文案')

    html = _replace_once(html, '双击物料，查看增长趋势及采购明细。', '双击物料，查看其月度采购趋势。', '空态提示')
    html = _replace_once(html, '双击物料查看趋势与明细', '双击物料查看趋势', '表格提示')
    html = _replace_once(html, '点击柱形或下方月份，查看对应入库明细。', '点击柱形或下方月份，切换查看该月概况。', '趋势图提示')
    html = _replace_once(html, '<h2 id="detailTitle">采购入库明细</h2>',
                         '<h2 id="detailTitle">采购入库明细（分享版不含）</h2>', '明细标题')
    html = _replace_once(html, '<span class="live-dot"></span>本地数据', '<span class="live-dot"></span>归档数据',
                         '数据来源标记')
    html = _replace_once(
        html,
        '  </style>',
        '  #detailSection .detail-filters,#detailSection .pager,'
        '#detailSection .table-scroll.details,#detailSection #allMonths{display:none}\n  </style>',
        '样式追加位')

    html = _replace_nav(html, 'increment')

    if source_style.removesuffix('</style>') not in style_block(html):
        raise SystemExit('样式校验失败：分享版 <style> 与本地页面不一致，请检查补丁顺序。')

    return inject_increment_blocks(html, payload)


def patch_savings_page(html: str, payload_json: str) -> str:
    source_style = style_block(html)
    html = inline_assets(html)

    html = _replace_once(
        html,
        "var response = await fetch('/api/savings-demo', { cache: 'no-store' });",
        "var response = { ok: true, json: async function () {"
        " return JSON.parse(document.getElementById('share-data').textContent) } };",
        '模拟数据接口')
    html = _replace_once(html, '<span class="head-note">本地数据 · 采购决策</span>',
                         '<span class="head-note">归档数据 · 采购决策</span>', '页头数据来源')
    html = _replace_nav(html, 'savings')

    if style_block(html) != source_style:
        raise SystemExit('样式校验失败：降本模拟分享版的 <style> 与本地页面不一致。')

    return inject_share_data(html, payload_json)


def export_savings_tables(out_dir: Path) -> list[str]:
    """把降本模拟库按真实表逐张导出，资料库里一张表一个节点。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    names = []
    with duckdb.connect(str(procurement_savings_demo.DATABASE), read_only=True) as con:
        for name in procurement_savings_demo.TABLES:
            frame = con.execute(
                f'SELECT * FROM {procurement_savings_demo.SCHEMA}.{name}').fetchdf()
            frame.to_csv(out_dir / f'{name}.csv', index=False, encoding='utf-8-sig')
            names.append(name)
    return names


def build_shell(increment_html: str, savings_html: str) -> str:
    def embed(doc: str) -> str:
        return re.sub(r'</script', DOC_TOKEN, doc, flags=re.IGNORECASE)

    return (SHELL.replace('@@INCREMENT@@', embed(increment_html))
                 .replace('@@SAVINGS@@', embed(savings_html))
                 .replace('@@TOKEN@@', DOC_TOKEN))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUTPUTS_ROOT / 'share',
                        help='输出目录，默认 outputs/share')
    args = parser.parse_args()

    ends = collect_periods()
    if not ends:
        raise SystemExit('outputs 尚无归档周期，请先运行 archive_procurement_increment.py')

    payload, frames = build_payload(ends)
    out = args.out
    increment_dir = out / '采购增量'
    increment_dir.mkdir(parents=True, exist_ok=True)
    for end, frame in frames.items():
        frame.to_csv(increment_dir / f'采购增量-{end[:7]}.csv',
                     index=False, encoding='utf-8-sig')

    savings_tables = export_savings_tables(out / '采购降本')

    increment_html = patch_page(SOURCE_PAGE.read_text(encoding='utf-8'), payload)
    fail = [name for name, ok in (
        ('增量：数据分块缺失', '<script id="share-meta"' in increment_html
         and '<script id="share-monthly"' in increment_html),
        ('增量：接口桩未生效', 'var SHARE_REPORTS={};' in increment_html),
        ('增量：仍有外部脚本引用', '/vendor/' not in increment_html),
        ('增量：场景导航未替换', 'parent.showView(\'savings\')' in increment_html),
    ) if not ok]

    savings_json = procurement_savings_demo.read_database().decode('utf-8')
    savings_html = patch_savings_page(SAVINGS_PAGE.read_text(encoding='utf-8'), savings_json)
    fail += [name for name, ok in (
        ('降本：数据块缺失', '<script id="share-data"' in savings_html),
        ('降本：接口桩未生效', 'ok: true, json: async function' in savings_html),
        ('降本：场景导航未替换', 'parent.showView(\'increment\')' in savings_html),
        ('降本：仍有外部脚本引用', '/vendor/' not in savings_html),
    ) if not ok]
    if fail:
        raise SystemExit('分享版自检失败：' + '、'.join(fail))

    page = build_shell(increment_html, savings_html)
    if page.count('</script') != 3:
        raise SystemExit('分享版自检失败：场景文档的脚本结束标记未正确转义。')
    if page.count('id="doc-increment"') != 1 or page.count('id="doc-savings"') != 1:
        raise SystemExit('分享版自检失败：场景文档缺失。')
    if '/vendor/' in page:
        raise SystemExit('分享版自检失败：仍有 /vendor/ 外链资源未内联。')
    if DOC_TOKEN not in page:
        raise SystemExit('分享版自检失败：场景文档未嵌入。')

    target = out / '供应链分析.html'
    target.write_text(page, encoding='utf-8')

    for stale in (out / '采购增量看板.html', out / '采购降本模拟.html', out / 'csv'):
        if stale.is_file():
            stale.unlink()
        elif stale.is_dir():
            for item in stale.iterdir():
                item.unlink()
            stale.rmdir()

    total_rows = sum(len(month['rows']) for month in payload['reports'].values())
    print(f'源页面：{SOURCE_PAGE.relative_to(PROJECT_ROOT)}、{SAVINGS_PAGE.relative_to(PROJECT_ROOT)}'
          '（整份复用，未另写页面）')
    print(f'周期 {len(ends)} 个（{ends[-1][:7]} → {ends[0][:7]}），物料 {total_rows:,} 行')
    print(f'月度趋势：{len(payload["monthly"]):,} 个物料，快照 {ends[0][:7]}')
    print(f'页面：{target}（{target.stat().st_size / 1e6:.2f} MB，含两个场景）')
    print(f'数据：{increment_dir}（{len(ends)} 张）、{out / "采购降本"}（{len(savings_tables)} 张）')


if __name__ == '__main__':
    main()
