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
SEAT_GROUPS = {
    "量化席位": ["摩根大通期货", "瑞银期货", "高盛期货", "东证期货", "海通期货"],
    "宏观席位": ["中信期货", "国泰君安期货"],
    "重点产业席位": ["中粮期货", "华泰期货", "国投安信期货", "瑞达期货", "五矿期货", "广发期货", "一德期货"],
}
SEATS = [seat for group in SEAT_GROUPS.values() for seat in group]

# ---- DeepSeek API 配置（需求5：AI 持仓简要分析，经后端 app.py 代理转发）----
# 注意：API key 已从前端/本脚本移除，改为由后端服务从环境变量 DEEPSEEK_API_KEY 读取
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_TIMEOUT_MS = 60000  # 接口超时兜底（毫秒）

SEAT_COLORS = {
    "摩根大通期货": "#E53935",
    "瑞银期货": "#FDD835",
    "高盛期货": "#C0CA33",
    "东证期货": "#8E24AA",
    "海通期货": "#00ACC1",
    "中信期货": "#FB8C00",
    "国泰君安期货": "#1E88E5",
    "中粮期货": "#43A047",
    "华泰期货": "#5E35B1",
    "国投安信期货": "#6D4C41",
    "瑞达期货": "#D81B60",
    "五矿期货": "#3949AB",
    "广发期货": "#00897B",
    "一德期货": "#F4511E",
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
  .seat-field { position: relative; }
  .seat-panel { display: none; position: absolute; top: 100%; left: 0; z-index: 300;
                width: 320px; max-height: 380px; overflow: auto; background: #fff;
                border: 1px solid #C5CBD3; border-radius: 6px; padding: 8px 10px;
                box-shadow: 0 4px 12px rgba(0,0,0,.12); font-size: 13px; }
  .seat-panel.open { display: block; }
  .seat-group-box { border-top: 1px solid #EEF2F7; padding: 6px 2px; }
  .seat-group-box:first-child { border-top: none; }
  .seat-group-box label.grp { display: block; font-weight: bold; color: #1E88E5; margin-bottom: 3px; }
  .seat-group-box label.item { display: block; font-weight: normal; padding: 2px 0 2px 18px; }
  .seat-group-box label.item input { margin-right: 4px; }
  #drawer-seat-panel { position: static; width: 100%; box-shadow: none; max-height: 260px; }
  #tables-area h4.sub { margin-top: 26px; }
  #tables-area table.summary { width: 100%; border-collapse: collapse; border: 1px solid #E3E7EC; font-size: 13px; margin-bottom: 18px; }
  #tables-area table.summary th { background: #EEF2F7; font-weight: bold; padding: 8px 12px; text-align: center; border-bottom: 1px solid #E3E7EC; }
  #tables-area table.summary td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #F0F0F0; cursor: pointer; }
  #tables-area table.summary td:first-child { text-align: left; font-weight: bold; }
  #tables-area table.summary tr.active { background: #FFF9C4; }
  #tables-area table.summary tr:hover { background: #F5F9FF; }
  #tables-area table.summary td.up { color: #E53935; }
  #tables-area table.summary td.down { color: #43A047; }
  #tables-area table.summary td.hot-up { background: #FFECB3; }
  #tables-area table.summary td.hot-down { background: #FFCDD2; }
  #tables-area table.summary tr.total-row td { background: #F7FAFF; font-weight: bold; border-top: 2px solid #C5CBD3; }
  .arbitrage-block { margin-top: 26px; border-top: 2px solid #E3E7EC; padding-top: 10px; }
  .arbitrage-block h3 { margin: 8px 0 12px; font-size: 16px; color: #222; }
  /* 套利板块：与上方汇总表同构，整表淡绿色背景区分 */
  #arbitrage-area table.summary { width: 100%; border-collapse: collapse; border: 1px solid #A5D6A7; font-size: 13px; margin-bottom: 18px; background: #E8F5E9; }
  #arbitrage-area table.summary th { background: #C8E6C9; font-weight: bold; padding: 8px 12px; text-align: center; border-bottom: 1px solid #A5D6A7; }
  #arbitrage-area table.summary td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #C8E6C9; cursor: default; }
  #arbitrage-area table.summary td:first-child { text-align: left; font-weight: bold; }
  #arbitrage-area table.summary td.up { color: #E53935; }
  #arbitrage-area table.summary td.down { color: #43A047; }
  #arbitrage-area .c-code { font-size: 11px; color: #888; }
  .arbitrage-block .hint { margin-top: 12px; }
  /* 需求5：AI 持仓简要分析板块 */
  .ai-block { margin-top: 26px; border-top: 2px solid #E3E7EC; padding-top: 10px; }
  .ai-block h3 { margin: 8px 0 12px; font-size: 16px; color: #222; }
  .ai-block .ai-head { display: flex; align-items: center; gap: 12px; margin: 10px 0 12px; }
  .ai-block .ai-risk { color: #C62828; font-weight: bold; margin-top: 10px; }
  #ai-result { margin-top: 4px; }
  #ai-result .ai-loading { color: #666; font-size: 13px; padding: 14px 12px; border: 1px dashed #C5CBD3; border-radius: 6px; background: #FAFBFC; }
  #ai-result .ai-error { color: #C62828; font-size: 13px; padding: 12px; border: 1px solid #FFCDD2; border-radius: 6px; background: #FFEBEE; white-space: pre-wrap; }
  #ai-result .ai-body { font-size: 13px; line-height: 1.7; padding: 14px 16px; border: 1px solid #E3E7EC; border-radius: 6px; background: #FBFCFE; white-space: pre-wrap; word-break: break-word; }
  /* 分组卡片式展示 */
  #ai-result .ai-cards { display: flex; flex-direction: column; gap: 10px; padding: 14px; background: #F2F6FF; border: 1px solid #D8E4F8; border-radius: 12px; }
  #ai-result .ai-card { background: #FFFFFF; border: 1px solid #E4EBFA; border-radius: 10px; padding: 10px 12px; box-shadow: 0 1px 3px rgba(40, 70, 140, 0.06); }
  #ai-result .ai-tag { display: inline-block; font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 999px; margin-bottom: 6px; }
  #ai-result .ai-tag-q { background: #E3EDFF; color: #2B5CD9; }
  #ai-result .ai-tag-h { background: #EFE6FF; color: #7A3FD0; }
  #ai-result .ai-tag-c { background: #E0F4F0; color: #0E8A70; }
  #ai-result .ai-tag-qq { background: #DCE9FF; color: #1B3F8F; }
  #ai-result .ai-tag-z { background: #F0E7DB; color: #8A5A2B; }
  #ai-result .ai-text { font-size: 13px; color: #2B2F36; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
  #ai-result .ai-para { font-size: 13px; color: #2B2F36; line-height: 1.7; padding: 2px 2px; }
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
    <button class="btn" id="drawer-seat-toggle" style="width:100%">选择席位（全部）</button>
    <div class="seat-panel" id="drawer-seat-panel"></div>
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
    <div class="field seat-field"><label>席位</label>
      <button class="btn" id="seat-toggle">选择席位（全部）</button>
      <div class="seat-panel" id="seat-panel"></div>
    </div>
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

  <div id="tables-area"></div>

  <div class="arbitrage-block">
    <h3>席位跨月套利（正套 / 反套）行为识别</h3>
    <div id="arbitrage-area"></div>
    <div class="hint">信号基于当日收盘净持仓计算；套利规模取配对方向两手数较小值；仅为统计推演，席位真实动机无法确认；仅计算 15、59、91 三组主力合约配对；仅供数据研究，不构成投资建议。</div>
  </div>

  <div class="ai-block">
    <h3>AI 持仓简要分析</h3>
    <div class="ai-head">
      <button class="btn primary" id="ai-run">生成 AI 分析</button>
      <span class="hint" style="margin:0">基于当前筛选状态（品种 / 合约 / 席位 / 周期 / 异动阈值）调用 DeepSeek 生成。</span>
    </div>
    <div id="ai-result"></div>
    <div class="hint ai-risk">AI 分析仅为数据统计解读，不构成任何投资建议</div>
  </div>

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
const SEAT_GROUPS = __SEAT_GROUPS__;
const SEAT_COLORS = __SEAT_COLORS__;
const DEEPSEEK_CONFIG = __DEEPSEEK_CONFIG__;
const MAX_TILES = 14;

const state = {
  variety: VARIETY_ORDER[0],
  contract: null,
  seats: ["中粮期货"],
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
  renderSeatPanel();
}

function renderSeatPanel() {
  const html = Object.keys(SEAT_GROUPS).map(g => {
    const items = SEAT_GROUPS[g].map(s => {
      const checked = state.seats.includes(s);
      return `<label class="item"><input type="checkbox" class="seat-item" value="${s}" ${checked ? "checked" : ""}>${s}</label>`;
    }).join("");
    const allChecked = SEAT_GROUPS[g].every(s => state.seats.includes(s));
    return `<div class="seat-group-box"><label class="grp"><input type="checkbox" class="seat-group" data-group="${g}" ${allChecked ? "checked" : ""}>${g}</label>${items}</div>`;
  }).join("");
  $("seat-panel").innerHTML = html;
  $("drawer-seat-panel").innerHTML = html;
  const n = state.seats.length;
  $("seat-toggle").textContent = "选择席位（" + n + "）";
  $("drawer-seat-toggle").textContent = "选择席位（" + n + "）";
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

function buildFig(v, c, seats) {
  const firstSer = DATA.data[v][c][seats[0]];
  if (!firstSer) return null;
  const sl = sliceSeries(firstSer);
  const multi = seats.length > 1;
  const traces = [
    {
      x: sl.dates, y: sl.close, type: "scatter", mode: "lines", name: "收盘价",
      line: { color: "#000000", width: 1.8 },
      customdata: sl.long.map((lg, i) => [lg, sl.short[i], sl.net[i], sl.net_change[i], sl.long_change[i], sl.short_change[i]]),
      hovertemplate: "日期 %{x}<br>收盘价 %{y:.1f}<br>多头增减仓 %{customdata[4]:+,.0f}<br>空头增减仓 %{customdata[5]:+,.0f}<extra></extra>",
      yaxis: "y",
    },
  ];
  seats.forEach(s => {
    const sd = DATA.data[v][c][s];
    if (!sd) return;
    const ssl = sliceSeries(sd);
    const color = SEAT_COLORS[s] || "#888";
    traces.push({
      x: ssl.dates, y: ssl.net, type: "scatter", mode: "lines", name: s + "净持仓",
      line: { color: color, width: multi ? 1.6 : 2.2 }, yaxis: "y2",
    });
    if (!multi) {
      traces.push({
        x: ssl.dates, y: ssl.net_change, type: "bar", name: "净增减仓",
        marker: { color: "#90CAF9" }, yaxis: "y2",
      });
      if (state.showMA) {
        traces.push({ x: ssl.dates, y: ssl.long, type: "scatter", mode: "lines", name: "多头持仓",
                      line: { color: "#E53935", width: 1.4, dash: "dot" }, yaxis: "y2" });
        traces.push({ x: ssl.dates, y: ssl.short, type: "scatter", mode: "lines", name: "空头持仓",
                      line: { color: "#43A047", width: 1.4, dash: "dot" }, yaxis: "y2" });
      }
    }
    if (state.threshold > 0) {
      const idxs = [];
      ssl.net_change.forEach((nc, i) => { if (Math.abs(nc) >= state.threshold) idxs.push(i); });
      if (idxs.length) {
        traces.push({
          x: idxs.map(i => ssl.dates[i]), y: idxs.map(i => ssl.net[i]), type: "scatter", mode: "markers",
          name: multi ? (s + "异动") : ("异动(≥" + state.threshold.toLocaleString() + ")"),
          customdata: idxs.map(i => ssl.net_change[i]),
          marker: { symbol: idxs.map(i => ssl.net_change[i] > 0 ? "triangle-up" : "triangle-down"),
                    size: multi ? 9 : 10, color: multi ? color : "#FF7043", line: { color: "#fff", width: multi ? 0.8 : 1 } },
          hovertemplate: (multi ? s + " 异动" : "异动") + " 净增减仓 %{customdata:+,.0f}<extra></extra>", yaxis: "y2",
        });
      }
    }
  });
  const seatLabel = multi ? (seats.length + "个席位") : seats[0];
  const layout = {
    title: { text: "【" + v + "：" + c + "】- " + seatLabel + " 持仓走势", font: { size: 15, color: "#222" } },
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
  const seatLabel = state.seats.length > 3 ? (state.seats.length + "个席位") : state.seats.join("/");
  if (state.mode === "B") t = "【" + state.variety + "】- " + seatLabel + " 多合约平铺";
  else if (state.mode === "C") t = "【" + state.variety + "：" + state.contract + "】- 同合约多席位平铺";
  else t = "【" + state.variety + "：" + state.contract + "】- " + seatLabel + " 持仓走势";
  $("title-bar").textContent = t + "　·　数据更新至 " + DATA.update_date;
}

function renderMain() {
  const fig = buildFig(state.variety, state.contract, state.seats);
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
    list = state.seats.filter(s => ser[s]);
  } else {
    list = contractsOf(state.variety);
  }
  list.forEach((item, i) => {
    const fig = state.mode === "C" ? buildFig(state.variety, state.contract, [item])
                                   : buildFig(state.variety, item, state.seats);
    if (!fig) return;
    const div = document.createElement("div");
    div.className = "tile";
    div.id = "tile-" + i;
    tilesEl.appendChild(div);
    Plotly.newPlot(div.id, fig.data, fig.layout, PLOT_CFG);
    div.on("plotly_click", function () {
      if (state.mode === "C") {
        state.seats = [item];
        renderSeatPanel();
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

function renderTables() {
  $("data-date").textContent = DATA.update_date;
  const rows = DATA.snapshot[state.variety] || [];
  const area = $("tables-area");
  let html = "";
  Object.keys(SEAT_GROUPS).forEach(g => {
    const seats = SEAT_GROUPS[g];
    html += `<h4 class="sub">${g}交易日汇总</h4>`;
    html += `<table class="summary" data-group="${g}"><thead><tr><th>合约代码</th><th>收盘价</th><th>涨跌</th>` +
            seats.map(s => `<th>${s.replace("期货", "")}净持仓</th>`).join("") +
            `</tr></thead><tbody>`;
    rows.forEach(r => {
      let chgCls = "", chgTxt = "-";
      if (r.chg !== null && r.chg !== undefined) {
        chgTxt = (r.chg > 0 ? "+" : "") + r.chg.toFixed(0);
        chgCls = r.chg > 0 ? "up" : (r.chg < 0 ? "down" : "");
      }
      const tds = [`<td>${r.contract}</td>`, `<td>${r.close === null ? "-" : r.close.toFixed(0)}</td>`,
                   `<td class="${chgCls}">${chgTxt}</td>`];
      seats.forEach(s => {
        const nc = r[s + "_chg"];
        let cls = "";
        if (state.threshold > 0 && nc !== null && nc !== undefined) {
          if (nc >= state.threshold) cls = "hot-up";
          else if (nc <= -state.threshold) cls = "hot-down";
        }
        tds.push(`<td class="${cls}">${r[s] === null ? "-" : Number(r[s]).toLocaleString()}</td>`);
      });
      html += `<tr data-contract='${r.contract}'>` + tds.join("") + "</tr>";
    });
    // 分组净持仓合计行：每个席位列 = 该席位在本组全部合约上的净持仓合计
    const totalTds = ["<td>分组净持仓合计</td>", "<td>-</td>", "<td>-</td>"];
    seats.forEach(s => {
      let sum = 0, has = false;
      rows.forEach(r => {
        const val = r[s];
        if (val !== null && val !== undefined) { sum += Number(val); has = true; }
      });
      totalTds.push(`<td>${has ? Number(sum).toLocaleString() : "-"}</td>`);
    });
    html += `<tr class="total-row">` + totalTds.join("") + "</tr>";
    html += "</tbody></table>";
  });
  area.innerHTML = html;
  area.querySelectorAll("tr[data-contract]").forEach(tr => {
    tr.addEventListener("click", function () {
      area.querySelectorAll("tr").forEach(x => x.classList.remove("active"));
      tr.classList.add("active");
      state.contract = tr.getAttribute("data-contract");
      $("contract").value = state.contract;
      document.querySelectorAll('input[name="mode"]').forEach(r => r.checked = (r.value === "A"));
      state.mode = "A";
      render();
    });
  });
}

// ---------- 需求4：席位跨月套利（正套/反套）行为识别 ----------
const ARBITRAGE_PAIRS = [
  { key: "15", near: "01", far: "05" },
  { key: "59", near: "05", far: "09" },
  { key: "91", near: "09", far: "next01" },
];

function contractYearMonth(contract) {
  const m = String(contract || "").match(/(\d{4})$/);
  if (!m) return null;
  return { year: parseInt(m[1].slice(0, 2), 10), month: m[1].slice(2) };
}

function contractByMonth(variety, month) {
  const pool = DATA.data[variety] || {};
  let best = null;
  Object.keys(pool).forEach(c => {
    const ym = contractYearMonth(c);
    if (ym && ym.month === month && (!best || ym.year > contractYearMonth(best).year)) best = c;
  });
  return best;
}

function contractNextYear(variety, baseContract, month) {
  const ym = contractYearMonth(baseContract);
  if (!ym) return null;
  const pool = DATA.data[variety] || {};
  let best = null;
  Object.keys(pool).forEach(c => {
    const cym = contractYearMonth(c);
    if (cym && cym.month === month && cym.year > ym.year) {
      if (!best || cym.year < contractYearMonth(best).year) best = c;
    }
  });
  return best;
}

function groupLatest(variety, contract, seats) {
  const out = { long: 0, short: 0, net: 0, longChange: 0, shortChange: 0, has: false };
  (seats || []).forEach(s => {
    const ser = DATA.data[variety] && DATA.data[variety][contract] && DATA.data[variety][contract][s];
    if (!ser || !ser.long || !ser.long.length) return;
    out.long += Number(ser.long[ser.long.length - 1]) || 0;
    out.short += Number(ser.short[ser.short.length - 1]) || 0;
    out.net += Number(ser.net[ser.net.length - 1]) || 0;
    out.longChange += Number(ser.long_change[ser.long_change.length - 1]) || 0;
    out.shortChange += Number(ser.short_change[ser.short_change.length - 1]) || 0;
    out.has = true;
  });
  return out;
}

function computeArbitrage() {
  const variety = state.variety;
  const result = { variety: variety, update_date: DATA.update_date, groups: {} };
  Object.keys(SEAT_GROUPS).forEach(g => {
    const seats = SEAT_GROUPS[g].filter(s => state.seats.includes(s));
    if (!seats.length) return;
    const c01 = contractByMonth(variety, "01");
    const c05 = contractByMonth(variety, "05");
    const c09 = contractByMonth(variety, "09");
    const c91far = contractNextYear(variety, c09, "01");
    const pairMap = {
      "15": { near: c01, far: c05, nearLabel: "01", farLabel: "05" },
      "59": { near: c05, far: c09, nearLabel: "05", farLabel: "09" },
      "91": { near: c09, far: c91far, nearLabel: "09", farLabel: "次年01" },
    };
    const rows = [];
    ARBITRAGE_PAIRS.forEach(p => {
      const pr = pairMap[p.key];
      const nearData = pr.near ? groupLatest(variety, pr.near, seats) : null;
      const farData = pr.far ? groupLatest(variety, pr.far, seats) : null;
      let signal = "无明显跨月套利信号", scale = 0;
      if (nearData && farData && nearData.has && farData.has) {
        const nLongC = nearData.longChange, fShortC = farData.shortChange;
        const nShortC = nearData.shortChange, fLongC = farData.longChange;
        if (nLongC > 0 && fShortC > 0) { signal = "偏向正套"; scale = Math.min(nLongC, fShortC); }
        else if (nShortC > 0 && fLongC > 0) { signal = "偏向反套"; scale = Math.min(nShortC, fLongC); }
      } else if (!pr.near || !pr.far) {
        signal = "无合约数据";
      }
      rows.push({
        nearLabel: pr.nearLabel, near: pr.near, far: pr.far,
        long: nearData ? nearData.long : null, short: nearData ? nearData.short : null,
        net: nearData ? nearData.net : null,
        longChange: nearData ? nearData.longChange : null, shortChange: nearData ? nearData.shortChange : null,
        pair: p.key + " 套利", signal: signal, scale: Math.round(scale),
      });
    });
    result.groups[g] = { seats: seats.slice(), rows: rows };
  });
  return result;
}

function renderArbitrage() {
  const area = $("arbitrage-area");
  if (!area) return;
  const result = computeArbitrage();
  window.arbitrageResult = result; // 预留数据源：供需求5 AI 分析模块读取
  const gNames = Object.keys(result.groups);
  if (!gNames.length) {
    area.innerHTML = '<div class="hint">未勾选任何席位，暂无套利识别数据。</div>';
    return;
  }
  let html = "";
  gNames.forEach(g => {
    const grp = result.groups[g];
    html += '<h4 class="sub">' + g + ' · 跨月套利识别</h4>';
    html += '<table class="summary arb-table"><thead><tr><th>合约</th><th>多头增/减仓</th><th>空头增/减仓</th><th>套利配对</th><th>套利信号</th><th>规模(手)</th></tr></thead><tbody>';
    grp.rows.forEach(r => {
      const changeTxt = v => v === null || v === undefined ? "-"
        : (v > 0 ? "+" : "") + Number(v).toLocaleString();
      const changeCls = v => v === null || v === undefined ? "" : (v > 0 ? "up" : (v < 0 ? "down" : ""));
      const scaleTxt = r.scale > 0 ? Number(r.scale).toLocaleString() : "0";
      html += '<tr><td>' + r.nearLabel + ' 合约' + (r.near ? '<br><span class="c-code">' + r.near + '</span>' : '') + '</td>'
        + '<td class="' + changeCls(r.longChange) + '">' + changeTxt(r.longChange) + '</td>'
        + '<td class="' + changeCls(r.shortChange) + '">' + changeTxt(r.shortChange) + '</td>'
        + '<td>' + r.pair + '</td><td>' + r.signal + '</td><td>' + scaleTxt + '</td></tr>';
    });
    html += '</tbody></table>';
  });
  area.innerHTML = html;
}

// ---------- 需求5：AI 持仓简要分析（后端 app.py 代理转发 DeepSeek） ----------
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function fmtN(v) {
  return (v === null || v === undefined) ? "-" : Number(v).toLocaleString();
}
function fmtSigned(v) {
  if (v === null || v === undefined) return "-";
  return (v > 0 ? "+" : "") + Number(v).toLocaleString();
}

function buildAIPrompt() {
  const L = [];
  L.push("你是国内商品期货席位持仓数据分析助手。请基于以下真实统计数据，输出一份结构化的「持仓简要分析」。");
  L.push("");
  L.push("===== 一、数据源与筛选状态 =====");
  L.push(`- 品种：${state.variety}`);
  L.push(`- 当前合约：${state.contract || "-"}`);
  L.push(`- 数据更新日期：${DATA.update_date}`);
  L.push(`- 所选时间周期：${state.range === "all" ? "全部" : state.range + " 个交易日"}`);
  L.push(`- 已勾选席位（${state.seats.length} 个）：${state.seats.join("、") || "无"}`);
  L.push(`- 异动阈值（净增减仓手数绝对值）：${state.threshold.toLocaleString()} 手`);
  L.push(`- 席位分组：量化席位=${SEAT_GROUPS["量化席位"].join("/")}；宏观席位=${SEAT_GROUPS["宏观席位"].join("/")}；重点产业席位=${SEAT_GROUPS["重点产业席位"].join("/")}`);

  const snap = DATA.snapshot[state.variety] || [];
  const pool = DATA.data[state.variety] || {};

  L.push("");
  L.push("===== 二、最新交易日快照（收盘价 / 涨跌 / 各席位净持仓(当日净增减仓)） =====");
  snap.forEach(r => {
    const cells = [r.contract, r.close === null ? "-" : r.close, r.chg === null ? "-" : fmtSigned(r.chg)];
    SEATS.forEach(s => {
      cells.push(r[s] === null ? "-" : fmtN(r[s]) + "(" + fmtSigned(r[s + "_chg"]) + ")");
    });
    L.push(cells.join(" | "));
  });

  L.push("");
  L.push("===== 三、三组席位汇总（各组在全部合约上的净持仓合计，最新交易日） =====");
  Object.keys(SEAT_GROUPS).forEach(g => {
    const seats = SEAT_GROUPS[g];
    let gsum = 0, ghas = false;
    snap.forEach(r => seats.forEach(s => { if (r[s] !== null && r[s] !== undefined) { gsum += Number(r[s]); ghas = true; } }));
    const cells = [g + "合计=" + (ghas ? fmtN(gsum) : "-")];
    seats.forEach(s => {
      let sum = 0, has = false;
      snap.forEach(r => { if (r[s] !== null && r[s] !== undefined) { sum += Number(r[s]); has = true; } });
      cells.push(s.replace("期货", "") + "=" + (has ? fmtN(sum) : "-"));
    });
    L.push(cells.join("；"));
  });

  L.push("");
  L.push("===== 四、总持仓量（14 个统计席位多空持仓合计，最新交易日） =====");
  Object.keys(pool).sort().forEach(c => {
    let total = 0;
    Object.keys(pool[c]).forEach(s => {
      const ser = pool[c][s];
      if (ser && ser.long && ser.long.length) {
        total += Number(ser.long[ser.long.length - 1]) + Number(ser.short[ser.short.length - 1]);
      }
    });
    L.push(c + " = " + fmtN(total) + " 手");
  });

  L.push("");
  L.push("===== 五、套利板块输出（window.arbitrageResult，基于最新交易日多空增减仓判断） =====");
  const arb = window.arbitrageResult || null;
  if (arb && Object.keys(arb.groups || {}).length) {
    Object.keys(arb.groups).forEach(g => {
      const grp = arb.groups[g];
      L.push("[" + g + "] 席位: " + grp.seats.join("、"));
      (grp.rows || []).forEach(r => {
        L.push(`  配对 ${r.pair}：近月 ${r.nearLabel}(${r.near || "-"}) 多头${fmtSigned(r.longChange)}/空头${fmtSigned(r.shortChange)}，` +
               `远月 ${r.farLabel}(${r.far || "-"})，信号=${r.signal}，估算规模=${fmtN(r.scale)}手`);
      });
    });
  } else {
    L.push("未勾选任何席位，无套利识别数据。");
  }

  L.push("");
  L.push("===== 六、所选周期持仓走势摘要（首日→末日） =====");
  L.push(`周期长度：${state.range === "all" ? "全部" : state.range + " 日"}`);
  Object.keys(pool).sort().forEach(c => {
    const seat0 = Object.keys(pool[c])[0];
    if (!seat0) return;
    const ser = pool[c][seat0];
    const n = state.range === "all" ? ser.dates.length : Math.min(Number(state.range), ser.dates.length);
    const st = ser.dates.length - n;
    const line = [];
    Object.keys(pool[c]).forEach(s => {
      const sd = pool[c][s];
      line.push(s.replace("期货", "") + ": 净持仓 " + fmtN(sd.net[st]) + "→" + fmtN(sd.net[ser.dates.length - 1]));
    });
    L.push(`【${c}】${ser.dates[st]}~${ser.dates[ser.dates.length - 1]} 收盘价 ${ser.close[st]}→${ser.close[ser.dates.length - 1]}；${line.join("；")}`);
  });

  L.push("");
  L.push("===== 七、最近 3 个交易日各席位连续增减仓明细（多空/净） =====");
  Object.keys(pool).sort().forEach(c => {
    const seat0 = Object.keys(pool[c])[0];
    if (!seat0) return;
    const ser = pool[c][seat0];
    const n3 = Math.min(3, ser.dates.length);
    L.push(`【${c}】最近 ${n3} 日（${ser.dates.slice(-n3).join(" / ")}）：`);
    Object.keys(pool[c]).forEach(s => {
      const sd = pool[c][s];
      L.push(`  ${s.replace("期货", "")}: 多头增减仓 ${sd.long_change.slice(-n3).map(fmtSigned).join("/")}；` +
             `空头增减仓 ${sd.short_change.slice(-n3).map(fmtSigned).join("/")}；净增减仓 ${sd.net_change.slice(-n3).map(fmtSigned).join("/")}`);
    });
  });

  L.push("");
  L.push("===== 八、异动清单（最新交易日净增减仓绝对值 ≥ 阈值） =====");
  let hotCount = 0;
  snap.forEach(r => {
    SEATS.forEach(s => {
      const nc = r[s + "_chg"];
      if (nc !== null && nc !== undefined && Math.abs(nc) >= state.threshold) {
        let lc = null, sc = null;
        const ser = pool[r.contract] && pool[r.contract][s];
        if (ser && ser.long_change && ser.long_change.length) {
          lc = ser.long_change[ser.long_change.length - 1];
          sc = ser.short_change[ser.short_change.length - 1];
        }
        L.push(`${r.contract} | ${s} | 净增减仓 ${fmtSigned(nc)} 手（多头 ${fmtSigned(lc)} / 空头 ${fmtSigned(sc)}）`);
        hotCount++;
      }
    });
  });
  if (!hotCount) L.push("当前阈值下无异动记录。");

  L.push("");
  L.push("===== 九、量化席位专项数据（摩根大通/瑞银/高盛/东证/海通，各合约 最新收盘价(涨跌) | 总持仓量 | 最近3日净增减仓） =====");
  const qSeats = SEAT_GROUPS["量化席位"] || [];
  Object.keys(pool).sort().forEach(c => {
    const seat0 = Object.keys(pool[c])[0];
    if (!seat0) return;
    const ser = pool[c][seat0];
    const snapRow = snap.find(r => r.contract === c);
    let totalOI = 0;
    Object.keys(pool[c]).forEach(s => {
      const sd = pool[c][s];
      if (sd && sd.long && sd.long.length) totalOI += Number(sd.long[sd.long.length - 1]) + Number(sd.short[sd.short.length - 1]);
    });
    const n3 = Math.min(3, ser.dates.length);
    const cellsQ = [];
    qSeats.forEach(s => {
      const sd = pool[c][s];
      if (!sd || !sd.net_change) return;
      cellsQ.push(s.replace("期货", "") + " 净增减 " + sd.net_change.slice(-n3).map(fmtSigned).join("/"));
    });
    L.push(`【${c}】收盘价 ${snapRow && snapRow.close != null ? snapRow.close : "-"}（${snapRow && snapRow.chg != null ? fmtSigned(snapRow.chg) : "-"}）| 总持仓 ${fmtN(totalOI)} 手 | ${cellsQ.join("；") || "无量化席位数据"}`);
  });

  L.push("");
  L.push("===== 十、中粮期货专项数据（各合约：收盘价 | 净持仓 | 最近3日多头/空头增减仓） =====");
  let zlCount = 0;
  Object.keys(pool).sort().forEach(c => {
    const ser = pool[c]["中粮期货"];
    if (!ser) return;
    const snapRow = snap.find(r => r.contract === c);
    const n3 = Math.min(3, ser.dates.length);
    L.push(`【${c}】收盘价 ${snapRow && snapRow.close != null ? snapRow.close : "-"} | 净持仓 ${fmtN(ser.net[ser.net.length - 1])} | 多头增减仓 ${ser.long_change.slice(-n3).map(fmtSigned).join("/")} | 空头增减仓 ${ser.short_change.slice(-n3).map(fmtSigned).join("/")}`);
    zlCount++;
  });
  if (!zlCount) L.push("当前品种/数据源中无中粮期货持仓数据，中粮专项基于可得数据分析。");

  L.push("");
  L.push("===== 十一、输出要求 =====");
  L.push("请按行输出结构化的持仓分析，直接说结论，口语化，参考风格：");
  L.push("「【量化外资】量化整体偏多，外资继续加多菜油11月、菜油1月及豆粕01合约，摩根大通单日净增3,879手。【宏观】宏观整体偏多，国泰君安、中信同步加多1月合约。【重点产业】重点产业偏中性，中粮5月连续加空、1月小幅回补，与91反套结构匹配。【量化席位专项】量化席位在豆油1月集体加多，对应总持仓抬升、期价走强。【中粮期货专项】中粮在豆粕01合约空头回补、棕榈油5月加空，节奏偏防守。AI 分析仅为数据统计解读，不构成任何投资建议。」");
  L.push("要求：");
  L.push("1. 每一行必须以【量化外资】【宏观】【重点产业】【量化席位专项】【中粮期货专项】五个标签之一开头，标签后紧跟内容；");
  L.push("2. 【量化外资】【宏观】【重点产业】每组先给整体多空倾向判断（如「量化整体偏多」），再给 1-2 句关键席位动作与显著异动（大额增/减仓席位及手数）；");
  L.push("3. 结合跨月正/反套信号（见第五节），说明席位持仓行为与跨月套利结构的匹配情况，可放在【重点产业】或专项中；");
  L.push("4. 【量化席位专项】1-3 句：联动各合约价格、总持仓量与量化席位连续增减仓，阐述价格变动与调仓的对应关系；");
  L.push("5. 【中粮期货专项】1-3 句：聚焦豆粕、棕榈油两品种，分析中粮各合约持仓与增减仓节奏，结合盘面价格解读产业端行为特征；");
  L.push("6. 每个标签输出 1-2 行，全文约 300-400 字；");
  L.push("7. 除上述 5 个标签外，不要输出任何其它标签、标题或分节；纯文本，禁止任何 Markdown 标记（如 #、**、*、-、` 等）；");
  L.push("8. 最后单独输出一行风险提示：「AI 分析仅为数据统计解读，不构成任何投资建议」（此行不带标签）；");
  L.push("9. 数据引用准确（手数带千分位），数据源缺失某项时基于可得数据分析，不得编造数据源中不存在的数字。");
  return L.join("\\n");
}

function stripMarkdown(text) {
  // 兜底清理：剥离常见 Markdown 符号，确保即使模型仍输出符号也能干净展示
  return String(text || "")
    .replace(/^#{1,6}\\s*/gm, "")                 // 行首 # 标题
    .replace(/^\\s*[\\-\\+\\*]\\s+/gm, "")        // 行首无序列表符（- / + / *）
    .replace(/^\\s*\\d+[\\.、\\)]\\s+/gm, "")     // 行首有序列表符（1. / 1、 / 1)）
    .replace(/\\*\\*/g, "")                       // ** 加粗
    .replace(/\\*/g, "")                          // 单个 *（斜体/星号）
    .replace(/`+/g, "")                           // 反引号
    .replace(/^\\s*>\\s?/gm, "")                  // 行首引用 >
    .replace(/[\\t ]+$/gm, "")                    // 行尾空白
    .replace(/\\n{3,}/g, "\\n\\n");               // 压缩多余空行
}

function renderAIResult(text) {
  // 将 AI 返回文本按行解析为分组卡片：行首【量化外资】【宏观】【重点产业】→ 卡片；其余行 → 普通段落
  const raw = String(text || "").trim();
  if (!raw) return '<div class="ai-body"></div>';
  const clean = stripMarkdown(raw);
  const lines = clean.split("\\n").map(function (s) { return s.trim(); }).filter(function (s) { return s.length > 0; });
  if (!lines.length) return '<div class="ai-body">' + escapeHtml(clean) + "</div>";
  const groupRe = /^【(量化外资|宏观|重点产业|量化席位专项|中粮期货专项)】/;
  const tagCls = { "量化外资": "q", "宏观": "h", "重点产业": "c", "量化席位专项": "qq", "中粮期货专项": "z" };
  let cards = "", extras = "";
  let recognized = 0;
  lines.forEach(function (line) {
    const m = line.match(groupRe);
    if (m) {
      recognized++;
      const g = m[1], body = line.slice(m[0].length).trim();
      cards += '<div class="ai-card"><span class="ai-tag ai-tag-' + (tagCls[g] || "x") + '">' + g + '</span><div class="ai-text">' + escapeHtml(body) + "</div></div>";
    } else {
      extras += '<div class="ai-para">' + escapeHtml(line) + "</div>";
    }
  });
  if (recognized > 0) return '<div class="ai-cards">' + cards + extras + "</div>";
  // 未识别到任何组标签：降级为原文整段显示
  return '<div class="ai-body">' + escapeHtml(clean) + "</div>";
}

async function runAIAnalysis() {
  const btn = $("ai-run"), out = $("ai-result");
  if (!btn || !out) return;
  btn.disabled = true;
  btn.textContent = "分析中…";
  out.innerHTML = '<div class="ai-loading">正在生成 AI 分析，请稍候（最长 ' + Math.round(DEEPSEEK_CONFIG.timeout_ms / 1000) + ' 秒）…</div>';
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, DEEPSEEK_CONFIG.timeout_ms);
  try {
    const prompt = buildAIPrompt();
    const resp = await fetch("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: prompt }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try { const j = await resp.json(); if (j && j.error) msg += " - " + j.error; } catch (e) {}
      throw new Error(msg);
    }
    const data = await resp.json();
    const content = data && data.content;
    if (!content) throw new Error("响应中未包含分析内容");
    const clean = stripMarkdown(content);
    out.innerHTML = renderAIResult(clean);
  } catch (err) {
    const timedOut = err && err.name === "AbortError";
    out.innerHTML = '<div class="ai-error">' + (timedOut
      ? "请求超时（" + Math.round(DEEPSEEK_CONFIG.timeout_ms / 1000) + " 秒），请稍后重试。"
      : "接口调用失败：" + escapeHtml(String(err && err.message ? err.message : err))) + "</div>";
  } finally {
    clearTimeout(timer);
    btn.disabled = false;
    btn.textContent = "生成 AI 分析";
  }
}

function render() {
  syncContractOptions();
  if (!state.contract) return;
  renderTitle();
  if (state.mode === "A") renderMain(); else renderTiles();
  renderTables();
  renderArbitrage();
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
  $("seat-toggle").addEventListener("click", e => { e.stopPropagation(); $("seat-panel").classList.toggle("open"); });
  $("drawer-seat-toggle").addEventListener("click", () => $("drawer-seat-panel").classList.toggle("open"));
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".seat-field")) $("seat-panel").classList.remove("open");
  });
  document.addEventListener("change", function (e) {
    if (e.target.classList && e.target.classList.contains("seat-group")) {
      const g = e.target.getAttribute("data-group");
      const gseats = SEAT_GROUPS[g] || [];
      if (e.target.checked) {
        gseats.forEach(s => { if (!state.seats.includes(s)) state.seats.push(s); });
      } else {
        state.seats = state.seats.filter(s => !gseats.includes(s));
      }
      renderSeatPanel();
      render();
    } else if (e.target.classList && e.target.classList.contains("seat-item")) {
      const s = e.target.value;
      if (e.target.checked) {
        if (!state.seats.includes(s)) state.seats.push(s);
      } else {
        state.seats = state.seats.filter(x => x !== s);
      }
      renderSeatPanel();
      render();
    }
  });
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
  $("ai-run").addEventListener("click", runAIAnalysis);
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
            .replace("__SEAT_GROUPS__", json.dumps(SEAT_GROUPS, ensure_ascii=False))
            .replace("__SEAT_COLORS__", json.dumps(SEAT_COLORS, ensure_ascii=False))
            .replace("__DEEPSEEK_CONFIG__", json.dumps({
                "model": DEEPSEEK_MODEL,
                "timeout_ms": DEEPSEEK_TIMEOUT_MS,
            }, ensure_ascii=False)))
    out = os.path.join(ROOT_DIR, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成: {out}")
    print(f"数据更新日期: {payload['update_date']} | 文件大小: {os.path.getsize(out) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
