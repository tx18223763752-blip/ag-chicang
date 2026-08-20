"""
三油两粕 · 席位持仓 一键整合脚本
================================================================
依次执行：
  1) ag_fetch_history.py   抓取/回补席位持仓数据 -> data/品种_汇总.csv + data/合约收盘价.csv
  2) gen_dashboard_html.py 读取 CSV 生成静态看板 dashboard.html

用法（与 ag_fetch_history.py 参数一致，透传）：
    python run_all.py
    python run_all.py --days 30
    python run_all.py --start 20260101 --end 20260817

原脚本零修改，输出内容与结构不变。
"""

import os
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(script, args):
    cmd = [sys.executable, os.path.join(ROOT_DIR, script)] + args
    print("\n===== 执行 {} =====".format(script))
    print(">>> " + " ".join(cmd))
    ret = subprocess.call(cmd, cwd=ROOT_DIR)
    if ret != 0:
        print("!! {} 执行失败（退出码 {}），终止后续步骤".format(script, ret))
        sys.exit(ret)
    return ret


def main():
    args = sys.argv[1:]
    run("ag_fetch_history.py", args)
    run("gen_dashboard_html.py", [])
    print("\n===== 全部完成 =====")
    print("看板文件: {}".format(os.path.join(ROOT_DIR, "dashboard.html")))


if __name__ == "__main__":
    main()
