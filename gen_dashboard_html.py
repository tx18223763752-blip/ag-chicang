# -*- coding: utf-8 -*-
"""
三油两粕 · 席位持仓静态看板生成脚本
================================================================
读取本地 CSV（data/品种_汇总.csv + data/合约收盘价.csv），
生成自包含的静态 HTML 看板 dashboard.html（内嵌数据 + 本地 Plotly.js）。

与爬虫脚本完全解耦，不内置任何抓取逻辑。
数据更新后重跑本脚本即可刷新看板：
    python gen_dashboard_html.py
然后双击打开 dashboard.html（无需服务器）。
"""

import json
import os

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")

VARIETY_ORDER = ["豆油", "棕榈油", "菜油", "菜粕", "豆粕"]
SEATS = ["中粮期货", "摩根大通期货", "乾坤期货", "瑞银期货",
         "永安期货", "中信期货", "国泰君安期货", "一德期货", "高盛期货"]
SEAT_COLORS = {
    "中粮期货": "#1E88E5",
    "摩根大通期货": "#E53935",
    "乾坤期货": "#43A047",
    "瑞银期货": "#FDD835",
    "永安期货": "#8E24AA",
    "中信期货": "#FB8C00",
    "国泰君安期货": "#00ACC1",
    "一德期货": "#6D4C41",
    "高盛期货": "#C0CA33",
}


def load_standard():
    """读取 CSV 并转换为标准字段 DataFrame（与 dashboard.py 同逻辑）。"""
    frames = []
    for variety in VARIETY_ORDER:
        fp = os.path.join(DATA_DIR, f"{variety}_汇总.csv")
        if os.path.exists(fp):
            frames.append(pd.read_csv(fp))
    if not frames:
        raise RuntimeError("未找到 data/ 下任何持仓 CSV，请先运行抓取脚本。")
    pos = pd.concat(frames, ignore_index=True)

    price_fp = os.path.join(DATA_DIR, "合约收盘价.csv")
    price = pd.read_csv(price_fp) if os.path.exists(price_fp) else None

    df = pos.copy()
    df["trading_date"] = pd.to_datetime(df["日期"].astype(str), format="%Y%m%d")
    df["variety"] = df["品种"]
    df["contract"] = df["合约"].astype(str).str.upper()
    df["seat_name"] = df["席位"]
    df["long_position"] = pd.to_numeric(df["多头持仓"], errors="coerce").fillna(0).astype(int)
    df["short_position"] = pd.to_numeric(df["空头持仓"], errors="coerce").fillna(0).astype(int)
    df["net_position"] = df["long_position"] - df["short_position"]

    if price is not None:
        price = price.copy()
        price["trading_date"] = pd.to_datetime(price["日期"].astype(str), format="%Y%m%d")
        price["variety"] = price["品种"]
        price["contract"] = price["合约"].astype(str).str.upper()
        p = price[["trading_date", "variety", "contract", "收盘价"]].rename(
            columns={"收盘价": "close_price"})
        df = df.merge(p, on=["trading_date", "variety", "contract"], how="left")
    else:
        df["close_price"] = None
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")

    df = df.sort_values(["seat_name", "variety", "contract", "trading_date"])
    df["net_change"] = (
        df.groupby(["seat_name", "variety", "contract"])["net_position"]
        .diff()
        .fillna(0)
        .astype(int)
    )
    df["long_change"] = (
        df.groupby(["seat_name", "variety", "contract"])["long_position"]
        .diff()
        .fillna(0)
        .astype(int)
    )
    df["short_change"] = (
        df.groupby(["seat_name", "variety", "contract"])["short_position"]
        .diff()
        .fillna(0)
        .astype(int)
    )
    return df


def build_payload(df):
    """组织为前端 JSON：data[品种][合约][席位] = 时间序列；snapshot[品种] = 最新快照。"""
    data = {}
    for variety in VARIETY_ORDER:
        sub = df[df["variety"] == variety]
        if sub.empty:
            continue
        data[variety] = {}
        for contract in sorted(sub["contract"].unique()):
            data[variety][contract] = {}
            for seat in SEATS:
                s = sub[(sub["contract"] == contract) & (sub["seat_name"] == seat)]
                if s.empty:
                    continue
                s = s.sort_values("trading_date")
                data[variety][contract][seat] = {
                    "dates": s["trading_date"].dt.strftime("%Y-%m-%d").tolist(),
                    "close": [None if pd.isna(x) else round(float(x), 1) for x in s["close_price"].tolist()],
                    "long": s["long_position"].tolist(),
                    "short": s["short_position"].tolist(),
                    "net": s["net_position"].tolist(),
                    "net_change": s["net_change"].tolist(),
                    "long_change": s["long_change"].tolist(),
                    "short_change": s["short_change"].tolist(),
                }

    snapshot = {}
    for variety in VARIETY_ORDER:
        sub = df[df["variety"] == variety]
        if sub.empty:
            continue
        last_date = sub["trading_date"].max()
        day = sub[sub["trading_date"] == last_date]
        prev_date = sub.loc[sub["trading_date"] < last_date, "trading_date"].max()
        prev = sub[sub["trading_date"] == prev_date] if pd.notna(prev_date) else None
        rows = []
        for contract in sorted(day["contract"].unique()):
            c = day[day["contract"] == contract]
            close = c["close_price"].dropna()
            close_val = round(float(close.iloc[-1]), 1) if not close.empty else None
            prev_close = None
            if prev is not None:
                pc = prev[(prev["contract"] == contract) & prev["close_price"].notna()]
                if not pc.empty:
                    prev_close = round(float(pc["close_price"].iloc[-1]), 1)
            chg = (close_val - prev_close) if (close_val is not None and prev_close is not None) else None
            row = {"contract": contract, "close": close_val, "chg": chg}
            for seat in SEATS:
                s = c[c["seat_name"] == seat]
                row[seat] = int(s["net_position"].iloc[0]) if not s.empty else None
                row[seat + "_chg"] = int(s["net_change"].iloc[0]) if not s.empty else None
            rows.append(row)
        snapshot[variety] = rows

    update_date = df["trading_date"].max().strftime("%Y-%m-%d") if not df.empty else "-"
    return {"update_date": update_date, "data": data, "snapshot": snapshot}


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>三油两粕 · 席位持仓看板</title>
<script>
__PLOTLY_JS__
</script>
<noscript><div style="color:#E53935;padding:10px;font-weight:bold">当前浏览器未启用 JavaScript，看板无法运行，请启用后刷新。</div></noscript>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", Arial, sans-serif; background: #FFFFFF; color: #222; min-height: 100vh; }
  .wrap { max-width: 1500px; margin: 0 auto; padding: 16px 24px 48px; }
  .drawer-btn { position: fixed; top: 16px; left: 16px; z-index: 1000; background: #1E88E5; color: #fff;
                border: none; border-radius: 6px; padding: 8px 14px; cursor: pointer; font-size: 14px; }
  .drawer { position: fixed; top: 0; left: 0; height: 100vh; width: 0; overflow: hidden; z-index: 999;
            background: #F7F9FC; border-right: 1px solid #E3E7EC; transition: width .25s ease; }
  .drawer.open { width: 264px; overflow: auto; }
  .drawer-inner { padding: 24px 16px; width: 230px; }
  .drawer h4 { margin: 0 0 12px; color: #1E88E5; }
  .drawer label { font-weight: bold; font-size: 13px; display: block; margin: 10px 0 4px; }
  .drawer select, .topbar select { width: 100%; padding: 6px 8px; border: 1px solid #C5CBD3; border-radius: 5px;
                                   background: #fff; font-size: 13px; }
  .drawer .btn-group { display: flex; gap: 6px; margin: 6px 0; }
  .topbar { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; padding: 16px 20px;
            border: 1px solid #E3E7EC; border-radius: 8px; background: #FBFCFE; margin-top: 56px; }
  .field label { display: block; font-size: 12px; color: #666; margin-bottom: 4px; font-weight: bold; }
  .field select { width: 150px; padding: 6px 8px; border: 1px solid #C5CBD3; border-radius: 5px; background: #fff; font-size: 13px; }
  .field .seat-sel { width: 170px; }
  .btn { background: #fff; color: #333; border: 1px solid #C5CBD3; border-radius: 5px; padding: 6px 12px;
         cursor: pointer; font-size: 13px; }
  .btn.primary { background: #1E88E5; color: #fff; border-color: #1E88E5; }
  .btn.active { background: #1E88E5; color: #fff; border-color: #1E88E5; }
  .chk { margin-top: 4px; font-size: 13px; }
  .chk input { margin-right: 4px; }
  #title-bar { margin: 18px 0 10px; font-size: 17px; color: #222; }
  .mode-row { display: flex; align-items: center; gap: 10px; margin: 8px 0 12px; font-size: 13px; }
  .mode-row label { font-weight: bold; }
  .mode-row input[type=radio] { margin: 0 4px 0 12px; }
  #chart-main { width: 100%; }
  #tiles { display: none; gap: 8px; overflow-x: auto; }
  #tiles .tile { flex: 0 0 560px; min-width: 540px; }
  h4.sub { margin: 22px 0 8px; font-size: 15px; }
  table#summary { width: 100%; border-collapse: collapse; border: 1px solid #E3E7EC; font-size: 13px; }
  #summary th { background: #EEF2F7; font-weight: bold; padding: 8px 12px; text-align: center; border-bottom: 1px solid #E3E7EC; }
  #summary td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #F0F0F0; cursor: pointer; }
  #summary td:first-child { text-align: left; font-weight: bold; }
  #summary tr.active { background: #FFF9C4; }
  #summary tr:hover { background: #F5F9FF; }
  #summary td.up { color: #E53935; }
  #summary td.down { color: #43A047; }
  #summary td.hot-up { background: #FFECB3; }
  #summary td.hot-down { background: #FFCDD2; }
  .hint { color: #999; font-size: 12px; margin-top: 6px; }
</style>
</head>
<body>
<button class="drawer-btn" id="drawer-toggle">☰ 菜单</button>
<div class="drawer" id="drawer">
  <div class="drawer-inner">
    <h4>快捷菜单</h4>
    <label>品种</label>
    <select id="drawer-variety"></select>
    <label>席位</label>
    <select id="drawer-seat"></select>
    <label>快捷周期</label>
    <div class="btn-group">
      <button class="btn" data-range="30">30日</button>
      <button class="btn" data-range="60">60日</button>
      <button class="btn" data-range="90">90日</button>
      <button class="btn" data-range="all">全部</button>
    </div>
    <label>显示模式</label>
    <div style="font-size:13px">
      <label><input type="radio" name="dmode" value="A" checked> 单合约大图</label><br>
      <label><input type="radio" name="dmode" value="B"> 多合约平铺</label><br>
      <label><input type="radio" name="dmode" value="C"> 同合约多席位平铺</label>
    </div>
  </div>
</div>

<div class="wrap">
  <div class="topbar">
    <div class="field"><label>品种</label><select id="variety"></select></div>
    <div class="field"><label>合约</label><select id="contract"></select></div>
    <div class="field"><label>席位</label><select id="seat" class="seat-sel"></select></div>
    <div class="field"><label>快捷周期</label>
      <div style="display:flex;gap:6px">
        <button class="btn" data-range="30">30日</button>
        <button class="btn" data-range="60">60日</button>
        <button class="btn" data-range="90" id="range-90">90日</button>
        <button class="btn" data-range="all">全部</button>
      </div>
    </div>
    <div class="field"><label>多头/空头持仓</label>
      <div class="chk"><input type="checkbox" id="show-ma"> 显示曲线</div>
    </div>
    <div class="field"><label>异动阈值(手)</label>
      <input type="number" id="threshold" value="10000" min="0" step="1000"
             style="width:110px;padding:5px 8px;border:1px solid #C5CBD3;border-radius:5px;font-size:13px">
    </div>
  </div>

  <h3 id="title-bar"></h3>

  <div class="mode-row">
    <label>浏览模式</label>
    <label><input type="radio" name="mode" value="A" checked> 单合约大图</label>
    <label><input type="radio" name="mode" value="B"> 多合约平铺</label>
    <label><input type="radio" name="mode" value="C"> 同合约多席位平铺</label>
  </div>

  <div id="chart-main"></div>
  <div id="tiles"></div>

  <h4 class="sub">最新交易日汇总（点击行切换合约）</h4>
  <table id="summary"></table>
  <div class="hint">数据更新日期：<span id="data-date"></span> · 更新数据后请重新运行 gen_dashboard_html.py 再刷新页面 · 图表支持框选放大 / 拖拽平移 / 右上角相机导出 PNG</div>
</div>

<script>
// 全局错误提示：任何脚本异常都会在页面顶部显示，避免静默空白难以排查
(function () {
  var banner = null;
  function show(msg) {
    try {
      if (!banner) {
        banner = document.createElement("div");
        banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#FFEBEE;color:#C62828;" +
          "padding:10px 16px;font-size:13px;font-family:Consolas,monospace;border-bottom:2px solid #E53935;white-space:pre-wrap;";
        document.body.appendChild(banner);
      }
      banner.textContent = "看板运行异常：" + msg;
    } catch (e) {}
  }
  window.addEventListener("error", function (e) {
    show((e.message || "未知错误") + (e.lineno ? " (第 " + e.lineno + " 行)" : ""));
  });
  window.addEventListener("unhandledrejection", function (e) {
    show("Promise 异常: " + (e.reason && e.reason.message ? e.reason.message : e.reason));
  });
  window.__showErr = show;
})();
const DATA = __DATA_JSON__;
const VARIETY_ORDER = __VARIETY_ORDER__;
const SEATS = __SEATS__;
const SEAT_COLORS = __SEAT_COLORS__;
const MAX_TILES = 9;

const state = {
  variety: VARIETY_ORDER[0],
  contract: null,
  seat: SEATS[0],
  range: 90,
  mode: "A",
  showMA: false,
  threshold: 10000,
};

const $ = (id) => document.getElementById(id);

function contractsOf(v) { return DATA.data[v] ? Object.keys(DATA.data[v]).sort() : []; }

function initSelects() {
  const vSel = $("variety"), dvSel = $("drawer-variety");
  vSel.innerHTML = VARIETY_ORDER.map(v => `<option value="${v}">${v}</option>`).join("");
  dvSel.innerHTML = vSel.innerHTML;
  $("seat").innerHTML = SEATS.map(s => `<option value="${s}">${s}</option>`).join("");
  $("drawer-seat").innerHTML = $("seat").innerHTML;
}

function syncContractOptions() {
  const list = contractsOf(state.variety);
  const cSel = $("contract");
  cSel.innerHTML = list.map(c => `<option value="${c}">${c}</option>`).join("");
  if (!list.includes(state.contract)) state.contract = list[0] || null;
  else cSel.value = state.contract;
}

function sliceSeries(ser) {
  if (state.range === "all" || !ser) return ser;
  const n = Math.min(Number(state.range), ser.dates.length);
  const st = ser.dates.length - n;
  return {
    dates: ser.dates.slice(st), close: ser.close.slice(st),
    long: ser.long.slice(st), short: ser.short.slice(st),
    net: ser.net.slice(st), net_change: ser.net_change.slice(st),
    long_change: ser.long_change.slice(st), short_change: ser.short_change.slice(st),
  };
}

function buildFig(v, c, s) {
  const ser = DATA.data[v][c][s];
  if (!ser) return null;
  const sl = sliceSeries(ser);
  const color = SEAT_COLORS[s];
  const traces = [
    {
      x: sl.dates, y: sl.close, type: "scatter", mode: "lines", name: "收盘价",
      line: { color: "#000000", width: 1.8 },
      customdata: sl.long.map((lg, i) => [lg, sl.short[i], sl.net[i], sl.net_change[i], sl.long_change[i], sl.short_change[i]]),
      hovertemplate: "日期 %{x}<br>收盘价 %{y:.1f}<br>多头增减仓 %{customdata[4]:+,.0f}<br>空头增减仓 %{customdata[5]:+,.0f}<extra></extra>",
      yaxis: "y",
    },
    {
      x: sl.dates, y: sl.net, type: "scatter", mode: "lines", name: s + "净持仓",
      line: { color: color, width: 2.2 }, yaxis: "y2",
    },
    {
      x: sl.dates, y: sl.net_change, type: "bar", name: "净增减仓",
      marker: { color: "#90CAF9" }, yaxis: "y2",
    },
  ];
  if (state.showMA) {
    traces.push({ x: sl.dates, y: sl.long, type: "scatter", mode: "lines", name: "多头持仓",
                  line: { color: "#E53935", width: 1.4, dash: "dot" }, yaxis: "y2" });
    traces.push({ x: sl.dates, y: sl.short, type: "scatter", mode: "lines", name: "空头持仓",
                  line: { color: "#43A047", width: 1.4, dash: "dot" }, yaxis: "y2" });
  }
  if (state.threshold > 0) {
    const idxs = [];
    sl.net_change.forEach((nc, i) => { if (Math.abs(nc) >= state.threshold) idxs.push(i); });
    if (idxs.length) {
      traces.push({
        x: idxs.map(i => sl.dates[i]), y: idxs.map(i => sl.net[i]), type: "scatter", mode: "markers",
        name: "异动(≥" + state.threshold.toLocaleString() + ")",
        customdata: idxs.map(i => sl.net_change[i]),
        marker: { symbol: idxs.map(i => sl.net_change[i] > 0 ? "triangle-up" : "triangle-down"),
                  size: 10, color: "#FF7043", line: { color: "#fff", width: 1 } },
        hovertemplate: "异动 净增减仓 %{customdata:+,.0f}<extra></extra>", yaxis: "y2",
      });
    }
  }
  const layout = {
    title: { text: "【" + s + "】-【" + v + "：" + c + "】- 席位持仓走势", font: { size: 15, color: "#222" } },
    paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", height: 560,
    margin: { l: 75, r: 85, t: 100, b: 45 }, hovermode: "x unified",
    legend: { orientation: "h", y: 1.02, x: 0, font: { size: 11 } },
    xaxis: { type: "category", tickangle: -45, nticks: 12, gridcolor: "#F2F2F2",
             showline: true, linecolor: "#E0E0E0", tickfont: { size: 9 } },
    yaxis: { title: "价格", gridcolor: "#F2F2F2", zeroline: false, tickformat: ".0f" },
    yaxis2: { title: "持仓手数", overlaying: "y", side: "right", gridcolor: "#FFFFFF", zeroline: false, tickformat: ".0f" },
    shapes: [{ type: "line", yref: "y2", y0: 0, y1: 0, xref: "paper", x0: 0, x1: 1,
               line: { color: "#BDBDBD", dash: "dash" } }],
    bargap: 0.08,
  };
  return { data: traces, layout };
}

const PLOT_CFG = { responsive: true, displaylogo: false,
                   toImageButtonOptions: { format: "png", filename: "席位持仓", scale: 2 } };

function renderTitle() {
  let t;
  if (state.mode === "B") t = "【" + state.seat + "】-【" + state.variety + "】多合约平铺";
  else if (state.mode === "C") t = "【" + state.variety + "：" + state.contract + "】- 同合约多席位平铺";
  else t = "【" + state.seat + "】-【" + state.variety + "：" + state.contract + "】- 席位持仓走势";
  $("title-bar").textContent = t + "　·　数据更新至 " + DATA.update_date;
}

function renderMain() {
  const fig = buildFig(state.variety, state.contract, state.seat);
  $("chart-main").style.display = "block";
  $("tiles").style.display = "none";
  if (fig) Plotly.react("chart-main", fig.data, fig.layout, PLOT_CFG);
}

function renderTiles() {
  $("chart-main").style.display = "none";
  const tilesEl = $("tiles");
  tilesEl.style.display = "flex";
  tilesEl.innerHTML = "";
  let list;
  if (state.mode === "C") {
    const ser = DATA.data[state.variety][state.contract] || {};
    list = SEATS.filter(s => ser[s]);
  } else {
    list = contractsOf(state.variety);
  }
  list.forEach((item, i) => {
    const fig = state.mode === "C" ? buildFig(state.variety, state.contract, item)
                                   : buildFig(state.variety, item, state.seat);
    if (!fig) return;
    const div = document.createElement("div");
    div.className = "tile";
    div.id = "tile-" + i;
    tilesEl.appendChild(div);
    Plotly.newPlot(div.id, fig.data, fig.layout, PLOT_CFG);
    div.on("plotly_click", function () {
      if (state.mode === "C") {
        state.seat = item;
        $("seat").value = item;
      } else {
        state.contract = item;
        $("contract").value = item;
      }
      state.mode = "A";
      document.querySelectorAll('input[name="mode"], input[name="dmode"]').forEach(r => r.checked = (r.value === "A"));
      render();
    });
  });
}

function renderTable() {
  const rows = DATA.snapshot[state.variety] || [];
  const table = $("summary");
  const head = "<tr><th>合约代码</th><th>收盘价</th><th>涨跌</th>" +
    SEATS.map(s => `<th>${s.replace("期货", "")}净持仓</th>`).join("") + "</tr>";
  const body = rows.map(r => {
    let chgCls = "", chgTxt = "-";
    if (r.chg !== null && r.chg !== undefined) {
      chgTxt = (r.chg > 0 ? "+" : "") + r.chg.toFixed(0);
      chgCls = r.chg > 0 ? "up" : (r.chg < 0 ? "down" : "");
    }
    const tds = [`<td>${r.contract}</td>`, `<td>${r.close === null ? "-" : r.close.toFixed(0)}</td>`,
                 `<td class="${chgCls}">${chgTxt}</td>`];
    SEATS.forEach(s => {
      const nc = r[s + "_chg"];
      let cls = "";
      if (state.threshold > 0 && nc !== null && nc !== undefined) {
        if (nc >= state.threshold) cls = "hot-up";
        else if (nc <= -state.threshold) cls = "hot-down";
      }
      tds.push(`<td class="${cls}">${r[s] === null ? "-" : Number(r[s]).toLocaleString()}</td>`);
    });
    return "<tr data-contract='" + r.contract + "'>" + tds.join("") + "</tr>";
  }).join("");
  table.innerHTML = head + body;
  table.querySelectorAll("tr[data-contract]").forEach(tr => {
    tr.addEventListener("click", function () {
      document.querySelectorAll("#summary tr").forEach(x => x.classList.remove("active"));
      tr.classList.add("active");
      state.contract = tr.getAttribute("data-contract");
      $("contract").value = state.contract;
      document.querySelectorAll('input[name="mode"]').forEach(r => r.checked = (r.value === "A"));
      state.mode = "A";
      render();
    });
  });
}

function render() {
  syncContractOptions();
  if (!state.contract) return;
  renderTitle();
  if (state.mode === "A") renderMain(); else renderTiles();
  renderTable();
  highlightRange();
}

function highlightRange() {
  document.querySelectorAll("[data-range]").forEach(b =>
    b.classList.toggle("active", String(state.range) === b.getAttribute("data-range")));
}

function bindEvents() {
  $("variety").addEventListener("change", e => {
    state.variety = e.target.value;
    state.contract = null;
    render();
  });
  $("drawer-variety").addEventListener("change", e => {
    state.variety = e.target.value;
    $("variety").value = state.variety;
    state.contract = null;
    render();
  });
  $("contract").addEventListener("change", e => { state.contract = e.target.value; render(); });
  $("seat").addEventListener("change", e => { state.seat = e.target.value; render(); });
  $("drawer-seat").addEventListener("change", e => { state.seat = e.target.value; $("seat").value = state.seat; render(); });
  document.querySelectorAll("[data-range]").forEach(b => b.addEventListener("click", () => {
    state.range = b.getAttribute("data-range");
    render();
  }));
  $("show-ma").addEventListener("change", e => { state.showMA = e.target.checked; render(); });
  $("threshold").addEventListener("change", e => {
    state.threshold = parseInt(e.target.value || "0", 10) || 0;
    render();
  });
  document.querySelectorAll('input[name="mode"]').forEach(r => r.addEventListener("change", e => {
    state.mode = e.target.value;
    render();
  }));
  document.querySelectorAll('input[name="dmode"]').forEach(r => r.addEventListener("change", e => {
    state.mode = e.target.value;
    document.querySelectorAll('input[name="mode"]').forEach(x => x.checked = (x.value === state.mode));
    render();
  }));
  $("drawer-toggle").addEventListener("click", () => $("drawer").classList.toggle("open"));
}

initSelects();
syncContractOptions();
bindEvents();
render();
</script>
</body>
</html>
"""


def main():
    df = load_standard()
    payload = build_payload(df)

    # 内嵌 Plotly.js：实现单文件自包含，文件被复制/发送到任何位置均可离线打开
    plotly_path = os.path.join(ROOT_DIR, "plotly.min.js")
    if not os.path.exists(plotly_path):
        raise RuntimeError("缺少 plotly.min.js，请先将其放置到脚本同目录。")
    with open(plotly_path, "r", encoding="utf-8", errors="ignore") as f:
        plotly_js = f.read()
    # 防止 </script> 序列截断内联脚本
    plotly_js = plotly_js.replace("</script", "<\\/script")

    html = (HTML_TEMPLATE
            .replace("__PLOTLY_JS__", plotly_js)
            .replace("__DATA_JSON__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            .replace("__VARIETY_ORDER__", json.dumps(VARIETY_ORDER, ensure_ascii=False))
            .replace("__SEATS__", json.dumps(SEATS, ensure_ascii=False))
            .replace("__SEAT_COLORS__", json.dumps(SEAT_COLORS, ensure_ascii=False)))
    out = os.path.join(ROOT_DIR, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {out}")
    print(f"数据更新日期: {payload['update_date']} | 文件大小: {os.path.getsize(out) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
