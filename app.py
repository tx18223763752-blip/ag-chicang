# -*- coding: utf-8 -*-
"""AG-ChiCang 持仓看板后端服务（方案B：后端代理转发 DeepSeek API）

职责：
- 提供 POST /api/ai 接口：接收 {prompt: "..."}，服务端读取环境变量
  DEEPSEEK_API_KEY 调用 DeepSeek Chat Completions，返回 {content: "..."}；
- 静态文件服务：根路径返回 dashboard.html，其余静态资源（plotly 等）一并托管；
- CORS 允许跨域（备用，前端使用相对路径同源访问时不受影响）。

本地运行：
  set DEEPSEEK_API_KEY=sk-xxx
  python app.py
  浏览器访问 http://localhost:8000

Render 部署（Web Service）：
  - 启动命令：python app.py
  - 环境变量：DEEPSEEK_API_KEY（必填）；可选 DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_TIMEOUT / PORT
"""
import json
import os

import requests
from flask import Flask, jsonify, request, send_from_directory

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=None)

# ---- 配置（全部来自环境变量，key 不写死在代码里） ----
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
TIMEOUT = float(os.environ.get("DEEPSEEK_TIMEOUT", "60"))
PORT = int(os.environ.get("PORT", "8000"))


@app.after_request
def add_cors_headers(resp):
    """CORS 允许跨域（备用：前端同源访问时不影响）"""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(ROOT_DIR, "dashboard.html")


@app.route("/<path:filename>", methods=["GET"])
def static_files(filename):
    return send_from_directory(ROOT_DIR, filename)


@app.route("/api/ai", methods=["POST", "OPTIONS"])
def ai_proxy():
    if request.method == "OPTIONS":
        return ("", 204)
    if not API_KEY:
        return jsonify({"error": "服务端未配置 DEEPSEEK_API_KEY 环境变量"}), 500

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "缺少 prompt 参数"}), 400

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.3,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + API_KEY,
    }
    try:
        resp = requests.post(BASE_URL + "/chat/completions", headers=headers, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return jsonify({"error": "DeepSeek 请求失败: " + str(exc)}), 502

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        return jsonify({"error": "DeepSeek 返回 " + str(resp.status_code), "detail": detail}), resp.status_code

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return jsonify({"error": "DeepSeek 响应解析失败", "detail": resp.json()}), 502
    return jsonify({"content": content})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
