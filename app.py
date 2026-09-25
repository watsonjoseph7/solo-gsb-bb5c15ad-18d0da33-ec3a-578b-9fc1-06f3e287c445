"""乐谱反复段展开 Web 应用（Flask）。"""

import os

from flask import Flask, jsonify, render_template, request

from repeats import MAX_MEASURES, expand, parse

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
