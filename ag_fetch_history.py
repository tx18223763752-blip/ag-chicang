# -*- coding: utf-8 -*-
"""
三油两粕 席位持仓历史回补 + 合约收盘价抓取脚本
================================================================
功能   :
  1) 从最新有数据交易日向前回补近 N 个交易日（默认 22 天≈1个月）
     的 4 席位(摩根大通期货/乾坤期货/瑞银期货/中粮期货)多空持仓，
     增量合并进 data/品种_汇总.csv（按 日期+品种+合约+席位 去重）；
  2) 抓取全部目标合约的新浪期货日K收盘价，写入 data/合约收盘价.csv。
数据源 :
  持仓  : 东方财富期货龙虎榜 API（大商所 market=114，郑商所 market=115）
          对无效日期接口会静默返回最近有数据日期，必须校验 tradeDate 一致
  价格  : 新浪期货日K接口 stock.finance.sina.com.cn（符号=东财合约代码大写）
用法   : python ag_fetch_history.py [--days 22] [--end 20260812]
输出   : E:\precip-excel\AG-ChiCang\data\
"""

import argparse
import datetime
import os
import re
import sys
import time

import requests
import pandas as pd

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(OUTPUT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

API_BASE = "https://qhhqzl.eastmoney.com/marketFutuWeb/dragonAndTigerInfo/"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://qhweb.eastmoney.com/",
}
TIMEOUT = 20
MAX_RETRY = 3

VARIETIES = {
    "豆油":   {"code": "y",  "market": "114"},
    "棕榈油": {"code": "p",  "market": "114"},
    "豆粕":   {"code": "m",  "market": "114"},
    "菜油":   {"code": "OI", "market": "115"},
    "菜粕":   {"code": "RM", "market": "115"},
}
SEAT_GROUPS = {
    "量化席位": ["摩根大通期货", "瑞银期货", "高盛期货", "东证期货", "海通期货"],
    "宏观席位": ["中信期货", "国泰君安期货"],
    "重点产业席位": ["中粮期货", "华泰期货", "国投安信期货", "瑞达期货", "五矿期货", "广发期货", "一德期货"],
}
SEATS = [seat for group in SEAT_GROUPS.values() for seat in group]
ALL_MONTHS = [1, 3, 5, 7, 9, 11]

CSV_COLUMNS = ["日期", "品种", "合约", "合约月份", "席位", "多头持仓", "空头持仓",
               "净持仓", "净持仓方向", "多头增减仓", "空头增减仓"]
PRICE_COLUMNS = ["品种", "合约", "合约月份", "日期", "收盘价"]


def log(msg):
    print("[{}] {}".format(datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def api_get(path, params):
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(API_BASE + path, params=params, headers=HEADERS, timeout=TIMEOUT)
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("接口请求失败 {}?{}: {}".format(path, params, last_err))


def fetch_position(contract, market, date_str):
    """抓取指定合约持仓排名；tradeDate 不一致视为无效（东财对无效日期会返回最近数据）"""
    j = api_get("getLongAndShortPosition",
                {"date": date_str, "contract": contract, "market": market})
    if j.get("code") != 10000:
        return None
    data = j.get("data") or {}
    if not data.get("longInfoList"):
        return None
    if str(data.get("tradeDate")) != str(date_str):
        return None
    return data


def probe_has_data(date_str):
    """探测某交易日是否有持仓数据：用豆油 y2609 合约探测"""
    try:
        return fetch_position("y2609", "114", date_str) is not None
    except Exception:
        return False


def collect_trade_dates(end_date, n=None, start_date=None):
    """从 end_date 向前收集交易日。
    - 给定 n：收集 n 个交易日；
    - 给定 start_date：收集 start_date(含) ~ end_date 之间全部交易日；
    - 两者都给定：以 start_date 为下界，最多 n 个。"""
    d = datetime.datetime.strptime(end_date, "%Y%m%d").date()
    stop = datetime.datetime.strptime(start_date, "%Y%m%d").date() if start_date else None
    dates = []
    guard = 0
    while guard < 400:
        if stop and d < stop:
            break
        if n and len(dates) >= n:
            break
        ds = d.strftime("%Y%m%d")
        if probe_has_data(ds):
            dates.append(ds)
            if guard % 10 == 0 or len(dates) % 30 == 0:
                log("有效交易日 {} : {}".format(len(dates), ds))
        d -= datetime.timedelta(days=1)
        guard += 1
    dates.reverse()
    return dates


def seat_match(full_name):
    if not full_name:
        return None
    clean = re.sub(r"[（(].*?[）)]", "", full_name).strip()
    for seat in SEATS:
        if seat == "摩根大通期货":
            if "摩根大通" in clean:
                return seat
        elif seat in clean or clean in seat:
            return seat
    return None


def fixed_contracts(code, market, months, today):
    """按月份生成目标合约（最近未到期）"""
    result = []
    for m in months:
        year = today.year
        if (year, m) < (today.year, today.month):
            year += 1
        for _ in range(3):
            contract = "{}{:02d}{:02d}".format(code, year % 100, m)
            if fetch_position(contract, market, today.strftime("%Y%m%d")) is not None:
                result.append((contract, "{:02d}".format(m)))
                break
            year += 1
        else:
            log("合约 {} {:02d} 未找到，跳过".format(code, m))
    return result


def build_records(variety_name, cfg, contract, contract_label, date_str):
    data = fetch_position(contract, cfg["market"], date_str)
    if data is None:
        return []
    long_map, short_map = {}, {}
    for row in data.get("longInfoList") or []:
        seat = seat_match(row.get("futureCompanyName"))
        if seat:
            long_map[seat] = (int(row.get("longNum") or 0), int(row.get("longChange") or 0))
    for row in data.get("shortInfoList") or []:
        seat = seat_match(row.get("futureCompanyName"))
        if seat:
            short_map[seat] = (int(row.get("shortNum") or 0), int(row.get("shortChange") or 0))
    records = []
    for seat in SEATS:
        lp, lc = long_map.get(seat, (0, 0))
        sp, sc = short_map.get(seat, (0, 0))
        net = lp - sp
        net_side = "净多" if net > 0 else ("净空" if net < 0 else "持平")
        records.append({
            "日期": date_str, "品种": variety_name, "合约": contract,
            "合约月份": contract_label, "席位": seat, "多头持仓": lp,
            "空头持仓": sp, "净持仓": net, "净持仓方向": net_side,
            "多头增减仓": lc, "空头增减仓": sc,
        })
    return records


def append_csv(filepath, records, subset):
    new_df = pd.DataFrame(records, columns=CSV_COLUMNS)
    if os.path.exists(filepath):
        old = pd.read_csv(filepath, dtype={"日期": str, "合约月份": str})
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=subset, keep="last")
        merged = merged.sort_values(["日期", "品种", "合约", "席位"]).reset_index(drop=True)
    else:
        merged = new_df
    merged.to_csv(filepath, index=False, encoding="utf-8-sig")
    return merged


# ---------------------------------------------------------------- 价格抓取（新浪）
def sina_daily(symbol):
    """新浪期货日K，返回 [{d,o,h,l,c,v}, ...] 或 []"""
    url = "https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/InnerFuturesNewService.getDailyKLine"
    try:
        r = requests.get(url, params={"symbol": symbol}, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        }, timeout=TIMEOUT)
        m = re.search(r"\((.*)\)", r.text, re.S)
        if not m:
            return []
        import json
        data = json.loads(m.group(1))
        if isinstance(data, list):
            return data
        return list(data.values()) if isinstance(data, dict) else []
    except Exception as e:
        log("新浪日K失败 {}: {}".format(symbol, repr(e)[:120]))
        return []


def fetch_prices(variety_name, cfg, contract, contract_label, start_date, end_date):
    """抓取单合约收盘价，返回记录列表"""
    symbol = contract.upper()  # 东财 y2609 -> 新浪 Y2609；OI2701 -> OI2701
    rows = sina_daily(symbol)
    recs = []
    for row in rows:
        d = row.get("d", "")[:10].replace("-", "")
        if not d or d < start_date or d > end_date:
            continue
        try:
            close = float(row.get("c"))
        except (TypeError, ValueError):
            continue
        recs.append({"品种": variety_name, "合约": contract,
                     "合约月份": contract_label, "日期": d, "收盘价": close})
    if recs:
        log("价格 {} 共 {} 条 ({})".format(symbol, len(recs), recs[0]["日期"] + "~" + recs[-1]["日期"]))
    else:
        log("价格 {} 无数据".format(symbol))
    return recs


def main():
    parser = argparse.ArgumentParser(description="三油两粕席位持仓历史回补 + 收盘价抓取")
    parser.add_argument("--days", type=int, default=22, help="回补交易日数（默认22≈1个月）")
    parser.add_argument("--start", type=str, default="",
                        help="起始交易日 YYYYMMDD，提供时回补 start~end 全部交易日（优先于 --days）")
    parser.add_argument("--end", type=str, default="",
                        help="截止交易日 YYYYMMDD，默认自动探测最近有数据日")
    args = parser.parse_args()

    # 截止日：默认今天，若今天无数据向前回退
    end = args.end or datetime.date.today().strftime("%Y%m%d")
    if not args.end:
        while not probe_has_data(end):
            d = datetime.datetime.strptime(end, "%Y%m%d").date() - datetime.timedelta(days=1)
            end = d.strftime("%Y%m%d")
        log("探测到最近有数据交易日: {}".format(end))

    if args.start:
        trade_dates = collect_trade_dates(end, start_date=args.start)
    else:
        trade_dates = collect_trade_dates(end, n=args.days)
    if not trade_dates:
        log("未收集到有效交易日，退出")
        sys.exit(1)
    start_date = trade_dates[0]
    log("回补区间: {} ~ {}（{} 个交易日）".format(start_date, trade_dates[-1], len(trade_dates)))

    # ---- 持仓回补 ----
    today = datetime.datetime.strptime(trade_dates[-1], "%Y%m%d").date()
    total_new = 0
    for variety_name, cfg in VARIETIES.items():
        months = ALL_MONTHS[:]
        contracts = fixed_contracts(cfg["code"], cfg["market"], months, today)
        for date_str in trade_dates:
            for contract, label in contracts:
                recs = build_records(variety_name, cfg, contract, label, date_str)
                if recs:
                    fp = os.path.join(DATA_DIR, "{}_汇总.csv".format(variety_name))
                    merged = append_csv(fp, recs, subset=["日期", "品种", "合约", "席位"])
                    total_new += len(recs)
        log("{} 持仓回补完成".format(variety_name))

    # ---- 价格抓取 ----
    price_recs = []
    for variety_name, cfg in VARIETIES.items():
        months = ALL_MONTHS[:]
        contracts = fixed_contracts(cfg["code"], cfg["market"], months, today)
        for contract, label in contracts:
            price_recs.extend(fetch_prices(variety_name, cfg, contract, label,
                                           start_date, trade_dates[-1]))
        time.sleep(0.5)

    if price_recs:
        price_fp = os.path.join(DATA_DIR, "合约收盘价.csv")
        pdf = pd.DataFrame(price_recs, columns=PRICE_COLUMNS)
        if os.path.exists(price_fp):
            old = pd.read_csv(price_fp, dtype={"日期": str, "合约月份": str})
            pdf = pd.concat([old, pdf], ignore_index=True)
            pdf = pdf.drop_duplicates(subset=["品种", "合约", "日期"], keep="last")
        pdf = pdf.sort_values(["品种", "合约", "日期"]).reset_index(drop=True)
        pdf.to_csv(price_fp, index=False, encoding="utf-8-sig")
        log("收盘价已写入: {}（共 {} 行）".format(price_fp, len(pdf)))

    log("全部完成：新增持仓 {} 条，区间 {}~{}".format(total_new, start_date, trade_dates[-1]))


if __name__ == "__main__":
    main()
