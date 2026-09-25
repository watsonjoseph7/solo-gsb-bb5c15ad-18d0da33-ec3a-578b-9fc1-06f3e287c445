"""乐谱反复段展开 Web 应用（Flask）。"""

import os

from flask import Flask, jsonify, render_template, request

from repeats import MAX_BEATS, MAX_MEASURES, expand, parse, query_beats

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


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
            "errors": [{
                "line": overflow["line"],
                "message": (
                    f"展开超过 {MAX_MEASURES} 小节上限，"
                    f"在第 {overflow['line']} 行（小节 {overflow['num']}）处停止"
                ),
            }],
        })

    return jsonify({"ok": True, "count": len(rows), "rows": rows})


def _parse_bound(value, name):
    """校验时间窗边界（毫秒），接受整数；bool/浮点/非数字一律拒绝。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{name}必须为整数毫秒值"
    if value < 0:
        return None, f"{name}不能为负数"
    return value, None


@app.post("/api/beats")
def api_beats():
    data = request.get_json(silent=True) or {}
    text = data.get("input", "")
    if not isinstance(text, str) or not text.strip():
        return jsonify({
            "ok": False,
            "errors": [{"line": None, "message": "输入为空，请逐行输入小节编号"}],
        })

    start_ms, err = _parse_bound(data.get("start_ms"), "起始时间")
    if err is not None:
        return jsonify({"ok": False, "errors": [{"line": None, "message": err}]})
    end_ms, err = _parse_bound(data.get("end_ms"), "结束时间")
    if err is not None:
        return jsonify({"ok": False, "errors": [{"line": None, "message": err}]})
    if end_ms <= start_ms:
        return jsonify({
            "ok": False,
            "errors": [{"line": None, "message": "结束时间必须晚于起始时间（时间窗左闭右开）"}],
        })

    root, errors = parse(text)
    if errors:
        return jsonify({"ok": False, "errors": errors})

    beats, overflow = query_beats(root, start_ms, end_ms)
    if overflow is not None:
        node = overflow["node"]
        if overflow["kind"] == "beats":
            message = (
                f"时间窗内拍点超过 {MAX_BEATS} 个上限，"
                f"在第 {node['line']} 行（小节 {node['num']}）处停止"
            )
        else:
            message = (
                f"展开超过 {MAX_MEASURES} 小节上限，"
                f"在第 {node['line']} 行（小节 {node['num']}）处停止"
            )
        return jsonify({
            "ok": False,
            "overflow": True,
            "errors": [{"line": node["line"], "message": message}],
        })

    # 仅返回展示字段（精确累计时间不外露）
    rows = [{
        "time_ms": b["time_ms"],
        "seq": b["seq"],
        "num": b["num"],
        "line": b["line"],
        "beat": b["beat"],
        "downbeat": b["downbeat"],
    } for b in beats]
    return jsonify({
        "ok": True,
        "count": len(rows),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "beats": rows,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
