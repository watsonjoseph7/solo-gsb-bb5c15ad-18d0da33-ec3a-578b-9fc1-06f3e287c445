"""乐谱反复段展开 Web 应用（Flask）。"""

import math
import os

from flask import Flask, jsonify, render_template, request

from repeats import MAX_BEATS, MAX_MEASURES, beats_in_window, expand, parse

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


def _measure_overflow_error(node):
    """展开小节数超限的错误条目。"""
    return {
        "line": node["line"],
        "message": (
            f"展开超过 {MAX_MEASURES} 小节上限，"
            f"在第 {node['line']} 行（小节 {node['num']}）处停止"
        ),
    }


def _parse_window(data):
    """校验起止毫秒参数，返回 (start_ms, end_ms, 错误描述)；通过时错误描述为 None。"""
    values = []
    for label, key in (("起始", "start_ms"), ("结束", "end_ms")):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, None, f"{label}毫秒必须是数字"
        try:
            value = float(value)
        except OverflowError:
            return None, None, f"{label}毫秒数值过大"
        if not math.isfinite(value):
            return None, None, f"{label}毫秒必须是有限数字"
        values.append(value)
    start_ms, end_ms = values
    if start_ms < 0:
        return None, None, "起始毫秒不能为负数"
    if end_ms <= start_ms:
        return None, None, "结束毫秒必须大于起始毫秒"
    return start_ms, end_ms, None


@app.post("/api/expand")
def api_expand():
    data = request.get_json(silent=True) or {}
    text = data.get("input", "")
    if not isinstance(text, str) or not text.strip():
        return jsonify({
            "ok": False,
            "errors": [{"line": None, "message": "输入为空，请逐行输入小节编号"}],
        })

    root, errors = parse(text)
    if errors:
        return jsonify({"ok": False, "errors": errors})

    rows, overflow = expand(root)
    if overflow is not None:
        return jsonify({
            "ok": False,
            "overflow": True,
            "errors": [_measure_overflow_error(overflow)],
        })

    return jsonify({"ok": True, "count": len(rows), "rows": rows})


@app.post("/api/beats")
def api_beats():
    data = request.get_json(silent=True) or {}
    text = data.get("input", "")
    if not isinstance(text, str) or not text.strip():
        return jsonify({
            "ok": False,
            "errors": [{"line": None, "message": "输入为空，请逐行输入小节编号"}],
        })

    root, errors = parse(text)
    if errors:
        return jsonify({"ok": False, "errors": errors})

    start_ms, end_ms, err = _parse_window(data)
    if err is not None:
        return jsonify({"ok": False, "errors": [{"line": None, "message": err}]})

    beats, overflow = beats_in_window(root, start_ms, end_ms)
    if overflow is not None:
        node = overflow["node"]
        if overflow["kind"] == "measures":
            error = _measure_overflow_error(node)
        else:
            error = {
                "line": node["line"],
                "message": (
                    f"时间窗内拍点超过 {MAX_BEATS} 上限，"
                    f"在第 {node['line']} 行（小节 {node['num']}）处停止"
                ),
            }
        return jsonify({"ok": False, "overflow": True, "errors": [error]})

    return jsonify({"ok": True, "count": len(beats), "beats": beats})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
