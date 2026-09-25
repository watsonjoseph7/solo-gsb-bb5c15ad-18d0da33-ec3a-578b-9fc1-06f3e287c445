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

# 单次时间窗查询返回的拍点数上限
MAX_BEATS = 5000

# 拍点落窗判断时吸收累计浮点误差的容差（毫秒，仅用于吸附数学上相等的边界）
_EPS_MS = 1e-6

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


def _iter_played(root, measure_limit=MAX_MEASURES):
    """按嵌套层级依次产出实际演奏到的小节（迭代实现，避免深嵌套递归溢出）。

    每个产出为 (小节节点, 起始精确毫秒, 时长精确毫秒, 本小节实际速度)。
    速度沿用上一实际演奏小节：带速度标记的小节每次演奏到时先切换速度，
    未标速度则沿用当前速度（初始 120 BPM）。
    当累计演奏小节数超过 measure_limit 时，生成器以导致超限的小节节点为
    返回值结束（迭代结束时通过 StopIteration.value 获取）。
    """
    bpm = INITIAL_BPM  # 当前速度，初始 120 BPM
    elapsed = 0.0  # 已累计的演奏时长（毫秒，保留精度）
    count = 0
    overflow = None
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
            # 带速度标记的小节每次演奏到时先切换速度；未标速度则沿用当前速度
            if node["bpm"] is not None:
                bpm = node["bpm"]
            duration = node["beats"] * 60000 / bpm
            yield node, elapsed, duration, bpm
            elapsed += duration
            count += 1
            if count > measure_limit:
                overflow = node
                break
        else:
            stack.append([node["children"], 0, node["count"]])
    return overflow


def expand(root, limit=MAX_MEASURES):
    """按嵌套层级展开为实际演奏顺序。

    返回 (rows, overflow)：
    - rows     为 [{"seq": 演奏序号, "num": 原小节编号, "line": 源行号,
                     "start_ms": 起始毫秒, "end_ms": 结束毫秒}, ...]；
      时长按 拍数×60/BPM 秒累计，累计时保留精度，起止毫秒四舍五入到最近毫秒
    - overflow 为导致超过 limit 的小节节点；未超限时为 None
    """
    rows = []
    gen = _iter_played(root, limit)
    while True:
        try:
            node, elapsed, duration, _bpm = next(gen)
        except StopIteration as exc:
            overflow = exc.value
            break
        rows.append({
            "seq": len(rows) + 1,
            "num": node["num"],
            "line": node["line"],
            "start_ms": _round_ms(elapsed),
            "end_ms": _round_ms(elapsed + duration),
        })
    return rows, overflow


def _beat_index_below(position, measure_start, beat_dur, n_beats):
    """返回满足 小节起点 + j*拍长 < position 的最大拍下标 j（0 起）。

    数学上等于拍点位置时按容差吸附（吸收累计浮点误差）；结果裁到 [-1, n_beats-1]，
    返回 -1 表示小节内没有拍点严格早于 position。
    """
    offset = position - measure_start
    raw = offset / beat_dur
    nearest = round(raw)
    if -1 <= nearest <= n_beats and abs(offset - nearest * beat_dur) <= _EPS_MS:
        idx = nearest - 1  # 该拍点与 position 相等（左闭右开不含），取前一拍
    else:
        idx = math.ceil(raw) - 1
    return max(-1, min(n_beats - 1, idx))


def query_beats(root, start_ms, end_ms,
                measure_limit=MAX_MEASURES, beat_limit=MAX_BEATS):
    """查询左闭右开时间窗 [start_ms, end_ms) 内的拍点。

    用未取整的累计时间判断拍点是否落窗；按小节跨度直接跳过窗外小节，
    不逐拍展开。返回 (beats, overflow)：
    - beats 为 [{"time_ms": 展示毫秒, "time": 精确毫秒, "seq": 演奏序号,
                 "num": 原小节编号, "line": 源行号, "beat": 拍序,
                 "downbeat": 是否首拍}, ...]
    - overflow 为 None，或 {"kind": "measures"|"beats", "node": 小节节点}，
      分别表示演奏小节数超限或窗内拍点数超限，node 为导致停止的小节节点
    """
    beats = []
    seq = 0  # 实际演奏小节序号（每次经过一个小节加一，含窗外小节）
    gen = _iter_played(root, measure_limit)
    while True:
        try:
            node, m_start, duration, bpm = next(gen)
        except StopIteration as exc:
            if exc.value is not None:
                return beats, {"kind": "measures", "node": exc.value}
            break
        seq += 1

        m_end = m_start + duration
        # 整节在窗口之前：跳过；整节在窗口之后（起点已到/越过窗右端）：后续无需再看
        if m_end <= start_ms + _EPS_MS:
            continue
        if m_start >= end_ms - _EPS_MS:
            break

        n_beats = node["beats"]
        beat_dur = 60000 / bpm
        # 拍点（0 基下标 j）位于 m_start + j*beat_dur；左闭取 t>=start 的最小 j，
        # 右开取 t<end 的最大 j，数学上等于边界时按容差吸附
        j_lo = _beat_index_below(start_ms, m_start, beat_dur, n_beats) + 1
        j_hi = _beat_index_below(end_ms, m_start, beat_dur, n_beats)
        too_many = False
        for j in range(j_lo, j_hi + 1):
            t = m_start + j * beat_dur
            beats.append({
                "time_ms": _round_ms(t),
                "time": t,
                "seq": seq,
                "num": node["num"],
                "line": node["line"],
                "beat": j + 1,
                "downbeat": j == 0,
            })
            # 小节内也可能包含海量拍点（超长小节），必须在拍循环内即时停止
            if len(beats) > beat_limit:
                too_many = True
                break
        if too_many:
            return beats, {"kind": "beats", "node": node}
    return beats, None
