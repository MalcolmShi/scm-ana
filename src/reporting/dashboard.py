#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 看板导出 —— 自包含单文件，离线可看、可交互。

设计要点
--------
1. **单文件、零外部依赖**：SVG 图表与数据全部内联，双击即可打开，不依赖 CDN、
   不依赖网络。同事拿到资料库里的链接或文件后，脱离 WorkBuddy 也能看。
2. **可交互**：优先级筛选、搜索、排序，让业务方自己定位物料，而不是翻 Excel。
3. **口径可追溯**：页面底部保留规则说明与来源，避免看板被当成结论而非证据。

看板内容
--------
- 运行摘要：分析窗口、准入门槛、三档分布
- 各优先级覆盖采购金额（柱状图）
- 优先谈判物料 Top 15（横向条形图）
- 全部报告物料明细表（筛选 + 搜索 + 排序）
- 指标口径与使用边界
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

#: 优先级配色（与 Excel 图表页保持一致）
PRIORITY_COLORS = {
    "优先谈判": "#C0392B",
    "观察": "#D68910",
    "不建议": "#5D6D7E",
}

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>采购增量看板 {end_month}</title>
<style>
  :root {{
    --bg: #F5F7FA;
    --panel: #FFFFFF;
    --border: #E1E6EC;
    --text: #1F2D3D;
    --muted: #6B7A8D;
    --accent: #1F4E78;
    --radius: 10px;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 0 48px;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB",
                 "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
    font-size: 14px; line-height: 1.6;
  }}
  .wrap {{ max-width: 1280px; margin: 0 auto; padding: 0 24px; }}
  header {{
    background: linear-gradient(135deg, #1F4E78 0%, #2E6DA4 100%);
    color: #fff; padding: 28px 0 24px; margin-bottom: 24px;
  }}
  header h1 {{ margin: 0 0 6px; font-size: 24px; font-weight: 600; letter-spacing: .5px; }}
  header .sub {{ opacity: .9; font-size: 13px; }}
  header .meta {{ margin-top: 12px; font-size: 12.5px; opacity: .92; }}
  header .meta span {{ margin-right: 20px; white-space: nowrap; }}
  .panel {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 22px; margin-bottom: 20px;
  }}
  .panel h2 {{
    margin: 0 0 4px; font-size: 16px; font-weight: 600; color: var(--accent);
  }}
  .panel .desc {{ margin: 0 0 16px; color: var(--muted); font-size: 12.5px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }}
  .kpi {{
    background: #F8FAFC; border: 1px solid var(--border);
    border-left: 3px solid var(--accent); border-radius: 8px; padding: 14px 16px;
  }}
  .kpi .label {{ font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
  .kpi .value {{ font-size: 26px; font-weight: 650; color: var(--accent); line-height: 1.15; }}
  .kpi .value small {{ font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 3px; }}
  .kpi.red {{ border-left-color: {c_priority}; }}
  .kpi.red .value {{ color: {c_priority}; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 900px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 14px; }}
  .toolbar input[type=search], .toolbar select {{
    padding: 7px 11px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 13px; font-family: inherit; background: #fff; color: var(--text);
  }}
  .toolbar input[type=search] {{ min-width: 230px; }}
  .chip {{
    padding: 6px 13px; border-radius: 20px; border: 1px solid var(--border);
    background: #fff; cursor: pointer; font-size: 12.5px; user-select: none;
    transition: all .15s;
  }}
  .chip:hover {{ border-color: var(--accent); }}
  .chip.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .chip .dot {{
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 6px; vertical-align: 1px;
  }}
  .table-scroll {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
  th, td {{ padding: 9px 11px; text-align: left; border-bottom: 1px solid #EDF1F5; white-space: nowrap; }}
  th {{
    background: #F1F5F9; color: var(--accent); font-weight: 600;
    position: sticky; top: 0; cursor: pointer; user-select: none;
  }}
  th:hover {{ background: #E6EDF3; }}
  th .arrow {{ opacity: .35; font-size: 10px; margin-left: 3px; }}
  tbody tr:hover {{ background: #F8FAFC; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .badge {{
    display: inline-block; padding: 2px 9px; border-radius: 11px;
    font-size: 11.5px; font-weight: 500; color: #fff;
  }}
  .empty {{ padding: 40px; text-align: center; color: var(--muted); }}
  .legend {{ font-size: 12.5px; color: var(--muted); margin-top: 12px; }}
  .legend .dot {{
    display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 5px 0 14px;
  }}
  .legend .dot:first-child {{ margin-left: 0; }}
  svg {{ display: block; max-width: 100%; }}
  .rules {{ margin: 0; }}
  .rules dt {{ font-weight: 600; color: var(--accent); margin-top: 12px; font-size: 13px; }}
  .rules dd {{ margin: 3px 0 0; color: #3D4A5C; font-size: 12.5px; }}
  .rules dt:first-child {{ margin-top: 0; }}
  .note {{
    background: #FFFBEB; border: 1px solid #FDE68A; border-left: 3px solid #D68910;
    border-radius: 6px; padding: 12px 14px; font-size: 12.5px; color: #7F6000; margin-top: 16px;
  }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 28px; }}
  .rowcount {{ color: var(--muted); font-size: 12.5px; margin-left: auto; }}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>采购增量谈判看板</h1>
    <div class="sub">识别采购量持续增长、具备谈判价值的物料 · 数据口径见页面底部</div>
    <div class="meta">
      <span>分析窗口 <strong>{window_start} ~ {window_end}</strong></span>
      <span>板块 <strong>{boards}</strong></span>
      <span>报告物料 <strong>{total_materials}</strong> 个</span>
      <span>生成于 {generated_at}</span>
    </div>
  </div>
</header>

<div class="wrap">

  <div class="panel">
    <h2>运行摘要</h2>
    <p class="desc">本轮识别的谈判候选规模。优先谈判 = 采购量已明显上台阶且近期增长仍在延续。</p>
    <div class="kpis">
      <div class="kpi red">
        <div class="label">优先谈判候选</div>
        <div class="value" id="kpiPriority">{priority_count}<small>个物料</small></div>
      </div>
      <div class="kpi red">
        <div class="label">优先谈判覆盖金额</div>
        <div class="value" id="kpiAmount">{priority_amount}<small>万元</small></div>
      </div>
      <div class="kpi">
        <div class="label">观察</div>
        <div class="value" id="kpiWatch">{watch_count}<small>个物料</small></div>
      </div>
      <div class="kpi">
        <div class="label">不建议</div>
        <div class="value" id="kpiLow">{low_count}<small>个物料</small></div>
      </div>
      <div class="kpi">
        <div class="label">覆盖物料总数</div>
        <div class="value" id="kpiTotal">{total_materials}<small>个</small></div>
      </div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <h2>各优先级覆盖采购金额</h2>
      <p class="desc">对比三档候选覆盖的最近12个月采购规模，判断优先谈判的总体价值。单位：万元</p>
      <div id="priorityChart"></div>
      <div class="legend" id="priorityLegend"></div>
    </div>
    <div class="panel">
      <h2>优先谈判物料 Top 15</h2>
      <p class="desc">按最近12个月采购金额排名，优先锁定金额最高的候选物料。单位：万元</p>
      <div id="topChart"></div>
    </div>
  </div>

  <div class="panel">
    <h2>谈判候选明细</h2>
    <p class="desc">点击表头可排序。默认按优先级 → 趋势指数 → 近期采购占比排序，与 Excel 报告一致。</p>
    <div class="toolbar">
      <span class="chip active" data-priority="全部">全部</span>
      {priority_chips}
      <input type="search" id="search" placeholder="搜索物料名称 / 编码 / 供应商…">
      <select id="sortKey">
        <option value="default">默认排序</option>
        <option value="最近12个月累计采购不含税金额(万)">按采购金额</option>
        <option value="采购量趋势指数">按趋势指数</option>
        <option value="最近3个月采购量占比">按近期采购占比</option>
        <option value="连续增长月数">按连续增长月数</option>
      </select>
      <span class="rowcount" id="rowcount"></span>
    </div>
    <div class="table-scroll">
      <table id="dataTable">
        <thead><tr>{table_headers}</tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="empty" id="emptyState" style="display:none">没有符合当前筛选条件的物料。</div>
    <div class="note">
      本看板提供的是采购增量证据和优先级，不等同于最终谈判结论。实际谈判还需结合市场价格、替代供应商、
      合同周期、质量表现和业务需求稳定性判断。优先核实增长是否来自一次性备货、新项目集中采购或临时补库。
    </div>
  </div>

  <div class="panel">
    <h2>指标口径与规则</h2>
    <p class="desc">看板所有数字的计算方式，与 Excel 报告「规则说明」工作表一致。</p>
    <dl class="rules">{rules_html}</dl>
  </div>

  <footer>数据来源：{data_source_label} · 口径方案见 {proposal_path}</footer>
</div>

<script>
const DATA = {payload};
const PRIORITY_COLORS = {colors};
const COLUMNS = {columns};

// ---- 状态 ----
let state = {{ priority: '全部', keyword: '', sortKey: 'default', sortDir: 'desc' }};

// ---- 工具 ----
const pct = v => (v === null || v === undefined || Number.isNaN(v)) ? '—' : (v * 100).toFixed(2) + '%';
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(d);
const esc = s => String(s === null || s === undefined ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// ---- 筛选与排序 ----
function filteredRows() {{
  const kw = state.keyword.trim().toLowerCase();
  let rows = DATA.rows.filter(r => {{
    if (state.priority !== '全部' && r.谈判优先级 !== state.priority) return false;
    if (!kw) return true;
    return [r.物料编码, r.物料名称, r.核心供应商名称, r.品牌, r.规格, r.五级分类]
      .some(f => String(f || '').toLowerCase().includes(kw));
  }});
  if (state.sortKey !== 'default') {{
    const dir = state.sortDir === 'asc' ? 1 : -1;
    rows = rows.slice().sort((a, b) => {{
      const av = a[state.sortKey], bv = b[state.sortKey];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'string') return av.localeCompare(bv, 'zh-CN') * dir;
      return (av - bv) * dir;
    }});
  }}
  return rows;
}}

// ---- 表格渲染 ----
function renderTable() {{
  const rows = filteredRows();
  const tbody = document.querySelector('#dataTable tbody');
  const empty = document.getElementById('emptyState');
  document.getElementById('rowcount').textContent = `显示 ${{rows.length}} / ${{DATA.rows.length}} 个物料`;

  if (!rows.length) {{
    tbody.innerHTML = '';
    empty.style.display = 'block';
    document.querySelector('#dataTable').style.display = 'none';
    return;
  }}
  empty.style.display = 'none';
  document.querySelector('#dataTable').style.display = '';

  tbody.innerHTML = rows.map(r => {{
    const color = PRIORITY_COLORS[r.谈判优先级] || '#5D6D7E';
    return `<tr>
      <td><span class="badge" style="background:${{color}}">${{esc(r.谈判优先级)}}</span></td>
      <td>${{esc(r.物料编码)}}</td>
      <td>${{esc(r.物料名称)}}</td>
      <td>${{esc(r.规格)}}</td>
      <td>${{esc(r.品牌)}}</td>
      <td>${{esc(r.五级分类)}}</td>
      <td>${{esc(r.核心供应商名称)}}</td>
      <td class="num">${{pct(r.单一供应商供货占比)}}</td>
      <td class="num">${{pct(r.采购量趋势指数)}}</td>
      <td class="num">${{pct(r.最近1个月环比增长率)}}</td>
      <td class="num">${{r.连续增长月数}}</td>
      <td class="num">${{pct(r.最近3个月采购量占比)}}</td>
      <td class="num">${{num(r['最近12个月累计采购不含税金额(万)'], 1)}}</td>
    </tr>`;
  }}).join('');
}}

// ---- SVG 图表 ----
function svgEl(w, h) {{
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${{w}} ${{h}}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', h);
  return svg;
}}
function addText(svg, x, y, text, opts = {{}}) {{
  const ns = 'http://www.w3.org/2000/svg';
  const t = document.createElementNS(ns, 'text');
  t.setAttribute('x', x); t.setAttribute('y', y);
  t.setAttribute('font-size', opts.size || 12);
  t.setAttribute('fill', opts.fill || '#3D4A5C');
  t.setAttribute('text-anchor', opts.anchor || 'start');
  t.setAttribute('dominant-baseline', opts.baseline || 'auto');
  if (opts.weight) t.setAttribute('font-weight', opts.weight);
  t.textContent = text;
  svg.appendChild(t);
  return t;
}}

function renderPriorityChart() {{
  const host = document.getElementById('priorityChart');
  host.innerHTML = '';
  const data = DATA.priority_summary;
  const W = 560, H = 300, padL = 74, padR = 24, padT = 22, padB = 46;
  const svg = svgEl(W, H);
  const max = Math.max(...data.map(d => d.采购金额), 1);
  const plotW = W - padL - padR, plotH = H - padT - padB;

  for (let i = 0; i <= 4; i++) {{
    const v = max / 4 * i;
    const y = padT + plotH - (v / max) * plotH;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', padL); line.setAttribute('x2', W - padR);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
    line.setAttribute('stroke', '#EDF1F5'); line.setAttribute('stroke-width', 1);
    svg.appendChild(line);
    addText(svg, padL - 8, y + 4, v.toLocaleString('zh-CN', {{ maximumFractionDigits: 0 }}),
      {{ anchor: 'end', fill: '#8A97A6', size: 11 }});
  }}

  const slot = plotW / data.length;
  const barW = Math.min(74, slot * 0.46);
  data.forEach((d, i) => {{
    const h = (d.采购金额 / max) * plotH;
    const x = padL + slot * i + (slot - barW) / 2;
    const y = padT + plotH - h;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x); rect.setAttribute('y', y);
    rect.setAttribute('width', barW); rect.setAttribute('height', Math.max(h, 1));
    rect.setAttribute('rx', 3);
    rect.setAttribute('fill', PRIORITY_COLORS[d.谈判优先级] || '#5D6D7E');
    rect.setAttribute('opacity', '0.88');
    svg.appendChild(rect);
    addText(svg, x + barW / 2, y - 7,
      d.采购金额.toLocaleString('zh-CN', {{ maximumFractionDigits: 0 }}),
      {{ anchor: 'middle', size: 12, weight: '600', fill: '#1F2D3D' }});
    addText(svg, x + barW / 2, padT + plotH + 20, d.谈判优先级, {{ anchor: 'middle', size: 12 }});
    addText(svg, x + barW / 2, padT + plotH + 36, `${{d.物料数}} 个物料`,
      {{ anchor: 'middle', size: 11, fill: '#8A97A6' }});
  }});
  addText(svg, padL, 12, '万元', {{ size: 11, fill: '#8A97A6' }});
  host.appendChild(svg);

  document.getElementById('priorityLegend').innerHTML = data
    .map(d => `<span class="dot" style="background:${{PRIORITY_COLORS[d.谈判优先级] || '#5D6D7E'}}"></span>${{d.谈判优先级}}`)
    .join('');
}}

function renderTopChart() {{
  const host = document.getElementById('topChart');
  host.innerHTML = '';
  const data = DATA.top15;
  if (!data.length) {{
    host.innerHTML = '<div class="empty">本轮没有命中「优先谈判」的物料。</div>';
    return;
  }}
  const rowH = 26, padT = 8, padB = 26, padL = 148, padR = 62;
  const W = 560, H = padT + data.length * rowH + padB;
  const svg = svgEl(W, H);
  const max = Math.max(...data.map(d => d.金额), 1);
  const plotW = W - padL - padR;

  data.forEach((d, i) => {{
    const y = padT + i * rowH;
    const w = (d.金额 / max) * plotW;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', padL); rect.setAttribute('y', y + 4);
    rect.setAttribute('width', Math.max(w, 2)); rect.setAttribute('height', rowH - 11);
    rect.setAttribute('rx', 2.5);
    rect.setAttribute('fill', PRIORITY_COLORS[d.优先级] || '#C0392B');
    rect.setAttribute('opacity', '0.85');
    svg.appendChild(rect);

    const label = d.名称.length > 15 ? d.名称.slice(0, 15) + '…' : d.名称;
    addText(svg, padL - 10, y + rowH / 2, label,
      {{ anchor: 'end', size: 11.5, baseline: 'middle' }});
    addText(svg, padL + w + 7, y + rowH / 2,
      d.金额.toLocaleString('zh-CN', {{ maximumFractionDigits: 0 }}),
      {{ size: 11, fill: '#5D6D7E', baseline: 'middle' }});
  }});
  addText(svg, padL, H - 8, '万元', {{ size: 11, fill: '#8A97A6' }});
  host.appendChild(svg);
}}

// ---- 事件绑定 ----
function init() {{
  renderPriorityChart();
  renderTopChart();
  renderTable();

  document.querySelectorAll('.chip[data-priority]').forEach(chip => {{
    chip.addEventListener('click', () => {{
      document.querySelectorAll('.chip[data-priority]').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.priority = chip.dataset.priority;
      renderTable();
    }});
  }});

  let timer;
  document.getElementById('search').addEventListener('input', e => {{
    clearTimeout(timer);
    timer = setTimeout(() => {{ state.keyword = e.target.value; renderTable(); }}, 160);
  }});

  document.getElementById('sortKey').addEventListener('change', e => {{
    state.sortKey = e.target.value;
    if (state.sortKey !== 'default') state.sortDir = 'desc';
    renderTable();
  }});

  document.querySelectorAll('#dataTable th[data-key]').forEach(th => {{
    th.addEventListener('click', () => {{
      const key = th.dataset.key;
      if (state.sortKey === key) {{
        state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
      }} else {{
        state.sortKey = key;
        state.sortDir = 'desc';
        document.getElementById('sortKey').value = key in
          {{'最近12个月累计采购不含税金额(万)':1,'采购量趋势指数':1,'最近3个月采购量占比':1,'连续增长月数':1}}
          ? key : 'default';
      }}
      document.querySelectorAll('#dataTable th .arrow').forEach(a => a.textContent = '');
      th.querySelector('.arrow').textContent = state.sortDir === 'desc' ? '▼' : '▲';
      renderTable();
    }});
  }});
}}
init();
</script>
</body>
</html>
"""

#: 明细表展示的列（顺序即表头顺序）
TABLE_COLUMNS = [
    ("谈判优先级", "优先级"),
    ("物料编码", "物料编码"),
    ("物料名称", "物料名称"),
    ("规格", "规格"),
    ("品牌", "品牌"),
    ("五级分类", "五级分类"),
    ("核心供应商名称", "核心供应商"),
    ("单一供应商供货占比", "单一供货占比"),
    ("采购量趋势指数", "趋势指数"),
    ("最近1个月环比增长率", "近1月环比"),
    ("连续增长月数", "连续增长月数"),
    ("最近3个月采购量占比", "近3月占比"),
    ("最近12个月累计采购不含税金额(万)", "12月金额(万)"),
]

#: 页面底部的规则说明
RULE_ITEMS = [
    ("分析范围", "仅覆盖检验、检验产品、诊断三个板块；以源数据最大采购年月为结束月份，向前取 12 个月。"),
    ("金额准入", "最近 6 个月累计采购不含税金额大于 5 万元的物料才进入后续分析。"),
    ("连续采购准入", "最近 3 个月存在零采购月份的物料直接排除，不参与优先级分类。"),
    ("优先谈判", "采购量趋势指数 ≥ 20%，且最近 1 个月环比增长率 ≥ 10% 或连续增长月数 ≥ 2。采购量已明显提升且近期增长仍在延续，应优先核实需求原因并准备谈判。"),
    ("观察", "未达到优先谈判条件，但采购量趋势指数 ≥ 10% 或最近 3 个月采购量占比 ≥ 30%。已出现增长信号或近期需求集中，建议按月跟踪。"),
    ("不建议", "不满足优先谈判或观察条件。增长信号不足，暂不建议因采购增量启动谈判。"),
    ("最近 1 个月环比增长率", "(本月采购量 − 上月采购量) / 上月采购量；仅当上月采购量大于 0 时计算。"),
    ("采购量趋势指数", "最近 3 个月平均采购量 / 前 3 个月平均采购量 − 1，判断近期采购量是否整体上台阶。"),
    ("连续增长月数", "截至当前月，采购量环比连续为正的月份数。"),
    ("最近 3 个月采购量占比", "最近 3 个月采购总量 / 最近 12 个月采购总量，判断需求是否集中在近期释放。"),
    ("单一供应商供货占比", "核心供应商采购金额 / 该物料 12 个月累计采购金额，辅助判断供应集中度。"),
]


def _clean(value: Any) -> Any:
    """把 NaN / inf 转成 JSON 友好的值。"""
    if value is None:
        return None
    if isinstance(value, (int, str, bool)):
        return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 10)


def export_html(result: Dict[str, Any], output_path: Path) -> Path:
    """生成自包含 HTML 看板。"""
    report_df: pd.DataFrame = result["report"]
    meta = result["meta"]
    cfg = result["config"]
    priority_summary: pd.DataFrame = result["priority_summary"]

    rows: List[Dict[str, Any]] = []
    for record in report_df.to_dict(orient="records"):
        row = {key: _clean(val) for key, val in record.items()}
        rows.append(row)

    priority_data = [
        {
            "谈判优先级": r["谈判优先级"],
            "物料数": int(r["物料数"]),
            "采购金额": float(r["采购金额"]),
        }
        for _, r in priority_summary.iterrows()
    ]

    top = report_df.loc[report_df["谈判优先级"] == "优先谈判"].nlargest(
        15, "最近12个月累计采购不含税金额(万)"
    )
    top_data = [
        {
            "名称": str(r["物料名称"]),
            "金额": float(r["最近12个月累计采购不含税金额(万)"]),
            "优先级": str(r["谈判优先级"]),
        }
        for _, r in top.iterrows()
    ]

    priority_counts = {d["谈判优先级"]: d["物料数"] for d in priority_data}
    priority_amounts = {d["谈判优先级"]: d["采购金额"] for d in priority_data}

    payload = {
        "rows": rows,
        "priority_summary": priority_data,
        "top15": top_data,
    }

    table_headers = "".join(
        f'<th data-key="{key}">{label}<span class="arrow"></span></th>' for key, label in TABLE_COLUMNS
    )

    chips = []
    for name in ("优先谈判", "观察", "不建议"):
        color = PRIORITY_COLORS.get(name, "#5D6D7E")
        count = priority_counts.get(name, 0)
        chips.append(
            f'<span class="chip" data-priority="{name}">'
            f'<span class="dot" style="background:{color}"></span>{name} {count}</span>'
        )
    priority_chips = "".join(chips)

    rules_html = "".join(
        f"<dt>{name}</dt><dd>{desc}</dd>" for name, desc in RULE_ITEMS
    )

    from datetime import datetime
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 页脚的数据来源与方案出处：尽量自动推导，避免写死
    boards = "/".join(cfg.get("boards", []))
    data_source_label = f"{meta.get('data_source', '采购入库')}明细"
    if boards:
        data_source_label += f"（{boards}）"
    proposal_path = "docs/采购增量分析.md"

    html = TEMPLATE.format(
        end_month=meta["window_end"],
        window_start=meta["window_start"],
        window_end=meta["window_end"],
        boards=" / ".join(cfg["boards"]),
        total_materials=len(rows),
        priority_count=priority_counts.get("优先谈判", 0),
        priority_amount=f"{priority_amounts.get('优先谈判', 0):,.1f}",
        watch_count=priority_counts.get("观察", 0),
        low_count=priority_counts.get("不建议", 0),
        generated_at=generated_at,
        priority_chips=priority_chips,
        table_headers=table_headers,
        rules_html=rules_html,
        payload=json.dumps(payload, ensure_ascii=False),
        colors=json.dumps(PRIORITY_COLORS, ensure_ascii=False),
        columns=json.dumps([c[0] for c in TABLE_COLUMNS], ensure_ascii=False),
        c_priority=PRIORITY_COLORS["优先谈判"],
        data_source_label=data_source_label,
        proposal_path=proposal_path,
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    print("本模块由 scripts/procurement_analysis.py 调用，不单独运行。")
