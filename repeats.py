"""反复段解析与展开核心逻辑。

输入格式（逐行）：
    [|:]小节编号[:|xN][拍数@BPM]
- 编号前的 |:         表示反复起点
- 编号后的 :|xN       表示反复终点，N 为重复次数（2~9）
- 反复段允许嵌套
- 行末可附时间标记：[拍数] 或 [拍数@BPM]；拍数省略时默认为 4，
  未标速度的小节沿用上一演奏小节的速度，初始速度 120 BPM
"""

import math
import re

# 展开小节数上限
MAX_MEASURES = 5000

# 时间窗内拍点数上限
MAX_BEATS = 5000

# 默认拍数与初始速度
DEFAULT_BEATS = 4
INITIAL_BPM = 120

# 行格式：可选的 |: 前缀 + 编号 + 可选的 :|xN 后缀（N 为 2~9）+ 可选的 [拍数@BPM]
LINE_RE = re.compile(
    r"^\s*(?:(\|:)\s*)?(\d+)"
    r"(?:\s*(:\|)\s*[xX]\s*([2-9]))?"
    r"(?:\s*\[([^\]]*)\])?"
    r"\s*$"
)

# 正整数（仅 ASCII 数字）
POSITIVE_INT_RE = re.compile(r"[0-9]+")

FORMAT_HINT = (
    "格式错误：应为 [|:]编号[:|xN][拍数@BPM]，N 为 2~9，"
    "例如 “|:12:|x3[4@120]”"
)


def parse_timing(content):
    """解析时间标记内容（方括号内的文本），返回 (拍数, BPM, 错误描述)。

    内容形如 “拍数” 或 “拍数@BPM”。校验通过时错误描述为 None；
    校验失败时拍数与 BPM 为 None，错误描述指出问题所在。
    """
    parts = content.split("@")
    if len(parts) > 2:
        return None, None, "时间标记格式错误：应为 [拍数] 或 [拍数@BPM]，例如 “[4@120]”"
    beats_s = parts[0].strip()
    if not POSITIVE_INT_RE.fullmatch(beats_s) or int(beats_s) == 0:
        return None, None, f"拍数必须为正整数，得到 “{beats_s}”"
    bpm = None
    if len(parts) == 2:
        bpm_s = parts[1].strip()
        if not POSITIVE_INT_RE.fullmatch(bpm_s) or int(bpm_s) == 0:
            return None, None, f"BPM 必须为正整数，得到 “{bpm_s}”"
        bpm = int(bpm_s)
    return int(beats_s), bpm, None


def _round_ms(value):
    """四舍五入到最近毫秒（半值向上）。"""
    return int(math.floor(value + 0.5))


def parse(text):
    """解析输入文本，构建嵌套树并校验。

    返回 (root, errors)：
    - root  为 {"type": "group", "count": 1, "children": [...]} 形式的根节点；
      小节节点为 {"type": "measure", "num": 编号, "line": 行号,
      "beats": 拍数, "bpm": 速度或 None（未标注时沿用上一演奏小节）}。
    - errors 为 [{"line": 行号, "message": 错误描述}, ...]，按行号排序。
    """
    errors = []
    root = {"type": "group", "count": 1, "children": [], "line": None}
    stack = [root]  # 当前嵌套层级的分组栈，栈底为根
    seen = {}  # 小节编号 -> 首次出现的行号

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue  # 跳过空行

        m = LINE_RE.match(raw)
        if not m:
            errors.append({"line": lineno, "message": FORMAT_HINT})
            continue

        start, num_s, _end, count_s, timing_s = m.groups()
        num = int(num_s)

        # 时间标记：[拍数] 或 [拍数@BPM]；校验失败时记录错误并按默认值继续构建结构
        beats, bpm = DEFAULT_BEATS, None
        if timing_s is not None:
            beats, bpm, err = parse_timing(timing_s)
            if err is not None:
                errors.append({"line": lineno, "message": err})
                beats, bpm = DEFAULT_BEATS, None

        # 编号唯一性校验
        if num in seen:
            errors.append({
                "line": lineno,
                "message": f"小节编号 {num} 重复，首次出现在第 {seen[num]} 行",
            })
        else:
            seen[num] = lineno

        # 反复起点：新建分组并压栈，本行小节属于该分组
        if start:
            group = {"type": "group", "count": None, "children": [], "line": lineno}
            stack[-1]["children"].append(group)
            stack.append(group)

        stack[-1]["children"].append(
            {"type": "measure", "num": num, "line": lineno,
             "beats": beats, "bpm": bpm}
        )

        # 反复终点：弹出当前分组并记录重复次数
        if count_s is not None:
            if len(stack) == 1:
                errors.append({
                    "line": lineno,
                    "message": "反复终点 :| 没有匹配的反复起点 |:",
                })
            else:
                group = stack.pop()
                group["count"] = int(count_s)
                group["end_line"] = lineno

    # 输入结束时仍未闭合的反复起点
    while len(stack) > 1:
        group = stack.pop()
        errors.append({
            "line": group["line"],
            "message": "反复起点 |: 没有匹配的反复终点 :|xN",
        })

    errors.sort(key=lambda e: e["line"])
    return root, errors


def _iter_measures(root):
    """按实际演奏顺序迭代产生小节节点（迭代实现，避免深嵌套递归溢出）。"""
    # 帧结构：[子节点列表, 下一个待处理下标, 剩余重复次数]
    stack = [[root["children"], 0, 1]]
    while stack:
        frame = stack[-1]
        children, idx, _ = frame
        if idx >= len(children):
            # 当前分组一轮结束：剩余次数减一后从头再来
            stack.pop()
            frame[2] -= 1
            if frame[2] > 0:
                frame[1] = 0
                stack.append(frame)
            continue
        node = children[idx]
        frame[1] += 1
        if node["type"] == "measure":
            yield node
        else:
            stack.append([node["children"], 0, node["count"]])


def expand(root, limit=MAX_MEASURES):
    """按嵌套层级展开为实际演奏顺序。

    返回 (rows, overflow)：
    - rows     为 [{"seq": 演奏序号, "num": 原小节编号, "line": 源行号,
                     "start_ms": 起始毫秒, "end_ms": 结束毫秒}, ...]；
      时长按 拍数×60/BPM 秒累计，累计时保留精度，起止毫秒四舍五入到最近毫秒
    - overflow 为导致超过 limit 的小节节点；未超限时为 None
    """
    rows = []
    bpm = INITIAL_BPM  # 当前速度，初始 120 BPM
    elapsed = 0.0  # 已累计的演奏时长（毫秒，保留精度）
    for node in _iter_measures(root):
        # 带速度标记的小节每次演奏到时先切换速度；未标速度则沿用当前速度
        if node["bpm"] is not None:
            bpm = node["bpm"]
        duration = node["beats"] * 60000 / bpm
        rows.append({
            "seq": len(rows) + 1,
            "num": node["num"],
            "line": node["line"],
            "start_ms": _round_ms(elapsed),
            "end_ms": _round_ms(elapsed + duration),
        })
        elapsed += duration
        if len(rows) > limit:
            return rows, node
    return rows, None


def beats_in_window(root, start_ms, end_ms,
                    measure_limit=MAX_MEASURES, beat_limit=MAX_BEATS):
    """列出时间窗 [start_ms, end_ms) 内的拍点（左闭右开）。

    落窗判断使用未取整的累计时间，展示毫秒按 _round_ms 规则取整；
    整小节在窗外时按小节跨度直接跳过，窗内拍点超过 beat_limit 时停止，
    以免大量反复或长小节耗尽服务资源。

    返回 (beats, overflow)：
    - beats    为 [{"time_ms": 展示毫秒, "seq": 演奏序号, "num": 原小节编号,
                     "beat": 拍序, "first": 是否首拍}, ...]
    - overflow 为 None，或 {"kind": "measures" | "beats",
                            "node": 导致停止的小节节点}
    """
    beats = []
    bpm = INITIAL_BPM  # 当前速度；反复回跳后未标速度的小节沿用上一实际演奏小节
    elapsed = 0.0  # 已累计的演奏时长（毫秒，保留精度）
    seq = 0  # 演奏序号
    for node in _iter_measures(root):
        if node["bpm"] is not None:
            bpm = node["bpm"]
        seq += 1
        beat_len = 60000 / bpm  # 每拍时长（毫秒）
        duration = node["beats"] * beat_len
        if elapsed >= end_ms:
            break  # 时间轴单调前进，之后的拍点都在窗外
        if seq > measure_limit:
            return beats, {"kind": "measures", "node": node}
        if elapsed + duration > start_ms:
            # 小节跨度与时间窗相交：按等差定位首个可能落窗的拍，
            # 避免逐拍扫过窗口之前的长小节
            first_k = max(0, math.ceil((start_ms - elapsed) / beat_len) - 1)
            for k in range(first_k, node["beats"]):
                t = elapsed + k * beat_len
                if t >= end_ms:
                    break
                if t >= start_ms:
                    beats.append({
                        "time_ms": _round_ms(t),
                        "seq": seq,
                        "num": node["num"],
                        "beat": k + 1,
                        "first": k == 0,
                    })
                    if len(beats) > beat_limit:
                        return beats, {"kind": "beats", "node": node}
        # 整小节在窗口之前时按小节跨度跳过，不逐拍展开
        elapsed += duration
    return beats, None
