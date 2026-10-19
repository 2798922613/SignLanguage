#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
骨骼 JSON → 数据集文件（自建手语数据集 · 第 2 步）

输入：dataset/skeleton/*.json（由 tools/extract_skeleton.py 或 skeleton-recorder.html 产出）
输出：
  1. 自建-templates.js        —— DTW 序列模板（CUSTOM_WORDS / CUSTOM_SEQS / CUSTOM_DTW）
                                 每词若干条原型轨迹，保留原始逐帧序列（14 维姿态 + 10 维手形），
                                 由 gesture-recognizer.js 的 matchSpan() 做带状 DTW 匹配
  2. dataset/processed/dataset.npz  —— 富特征数据集 X[N, L, 146]，供 DTW / ST-GCN 使用
  3. dataset/processed/labels.json  —— 标签表、维度、每词条数、划分建议
  4. dataset/processed/report.md    —— 采集质量报告（检出率、时长分布、样本不足的词）

18 维特征（模板用，与现有识别代码一致）：
    ElbowL(3), WristL(3), HandL(3), ElbowR(3), WristR(3), HandR(3)
    归一化：中心=双肩中点(pose 11/12)，尺度=肩宽(3D)，x/y 取负以对齐 Kinect 坐标系
    HandL/HandR：有手部关键点时取中指 MCP(索引 9)，按手腕 x 与肩中心 x 的左右关系分配

146 维富特征（npz 用，给 DTW / 神经网络）：
    左手 21×3 + 右手 21×3 + 缺失掩码 2 + [肘L,腕L,肘R,腕R,髋L,髋R] 6×3

用法：
    python3 tools/build_dataset.py
    python3 tools/build_dataset.py --proto 5 --rich-length 48
    python3 tools/build_dataset.py --no-trim --js 自建-templates.js
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

# ---- 与 gesture-recognizer.js 严格对齐的常量 ----
NUM_KEYFRAMES = 10          # matchSLR500() 里写死的 numKeyframes
NUM_FEATURES = 14           # extractPoseFeatures() 的输出维度（2D + 手掌尺度）
SW_MIN = 0.01               # if (sw < 0.01) return null
RICH_LEN_DEFAULT = 40       # 对齐 BUFFER_SIZE = 40
RICH_DIM = 21 * 2 * 2 + 2 + 6 * 2   # 98（2D）


def norm_point(p, c, sw):
    """JS: [-(x-cx)/sw, -(y-cy)/sw]（2D，跨运行时稳定）"""
    return [-(p[0] - c[0]) / sw, -(p[1] - c[1]) / sw]


def frame_context(pose):
    """返回 (center, shoulder_width) 或 None；对应 JS 的前置校验。"""
    if not pose or len(pose) < 25:
        return None
    ls, rs = pose[11], pose[12]
    if ls is None or rs is None:
        return None
    le, re_, lw, rw = pose[13], pose[14], pose[15], pose[16]
    if (le is None or lw is None) and (re_ is None or rw is None):
        return None
    c = ((ls[0] + rs[0]) / 2.0, (ls[1] + rs[1]) / 2.0, 0.0)
    # 只用 2D 肩宽：MediaPipe 的 z 在 Python/JS 两版运行时之间不稳定，会制造域差
    sw = float(np.sqrt((ls[0] - rs[0]) ** 2 + (ls[1] - rs[1]) ** 2))
    if sw < SW_MIN:
        return None
    return c, sw


def split_hands(hands, cx):
    """按手腕 x 与肩中心 x 的关系把 0–2 只手分到 左/右 槽位（与 JS 同规则）。"""
    hl = hr = None
    for hand in hands or []:
        pts = hand.get('pts') or []
        if len(pts) < 21:
            continue
        if pts[0][0] < cx:
            hr = pts          # 图像左侧 → 受试者右手
        else:
            hl = pts
    return hl, hr


def feature18(pose, hands):
    """复刻 extractPoseFeatures()：返回 18 维 list 或 None。"""
    ctx = frame_context(pose)
    if ctx is None:
        return None
    c, sw = ctx
    ls, rs = pose[11], pose[12]
    le, re_ = pose[13], pose[14]
    lw, rw = pose[15], pose[16]

    hand_l, hand_r = lw, rw
    hl_pts, hr_pts = split_hands(hands, c[0])
    if hl_pts is not None:
        hand_l = hl_pts[9]
    if hr_pts is not None:
        hand_r = hr_pts[9]

    def palm_scale(pts):
        """手掌尺度 = |腕-中指MCP| / 肩宽：手越靠近摄像头越大，作为深度线索"""
        if pts is None:
            return 0.0
        return float(np.sqrt((pts[0][0] - pts[9][0]) ** 2 + (pts[0][1] - pts[9][1]) ** 2)) / sw

    out = []
    for elbow, wrist, hand, pts in ((le or ls, lw or ls, hand_l or lw or ls, hl_pts),
                                    (re_ or rs, rw or rs, hand_r or rw or rs, hr_pts)):
        out.extend(norm_point(elbow, c, sw))
        out.extend(norm_point(wrist, c, sw))
        out.extend(norm_point(hand, c, sw))
        out.append(palm_scale(pts))
    return out


SHAPE_DIM = 10          # 左/右手各 5 个手指伸展比；缺手填 -1
SHAPE_WEIGHT = 0.6      # 匹配时形状距离的权重（与 gesture-recognizer.js 保持一致）

# ---- DTW 序列模板参数（与 gesture-recognizer.js 的 matchSpan() 完全一致）----
TPL_MAXLEN = 48         # 单个原型序列的最大帧数（超出则线性下采样）
STILL_FLOOR = 0.0020    # 静止判定下限
STILL_CEIL = 0.0050     # 静止判定上限
ENV_MS = 150            # 运动能量包络窗口（毫秒），与 gesture-recognizer.js 一致
ENV_K = 0.30            # 包络阈值 = 中位 + ENV_K ×(p95-中位)
ENV_FLOOR = 1.30        # 包络阈值不低于中位的该倍数
HOLD_GAP_MS = 1000      # 手势内部允许的最长"保持"间隔（动作1→保持→动作2 算一个手势）
LEAD_PAD_MS = 250       # 运动跨度两端各保留的余量
MIN_SPAN_MS = 250       # 短于此不算手势
MAX_SPAN_MS = 6000      # 单次手势最长跨度
MIN_SPAN_HAND_RATIO = 0.30  # 核心段整体有手帧比例下限（起势时手常还没进画面，故只设低门槛）
TAIL_HAND_RATIO = 0.60      # 核心段**末 1/3**有手帧比例下限：手势收尾时手还在画面里，
                            # 而"打完手放下"的段收尾时手已经出画。实测两者是 100% vs 0%，
                            # 比整体比例（58%~94% vs 8%~45%，区间重叠）可靠得多。
TRIM_VARIANTS = (0.0, 0.10)         # 单样本词的时间对齐变体（容忍起收时机差异）
AUG_SCALES = (0.88, 1.0, 1.12)      # 幅度增广：手臂相对肩中心缩放（同一手势做大/做小）
XY_DIMS = tuple([i for i in range(NUM_FEATURES) if i not in (6, 13)])   # 姿态里的 xy 维（排除手掌尺度）
MAX_PROTO_PER_WORD = 6
MAX_SEQ_PER_WORD = 6              # 每词最多取用的样本条数


def finger_ratios(pts):
    """5 个尺度无关的伸展比（2D）：拇指 |4-2|/|3-2|，其余 |tip-mcp|/|pip-mcp|，上限 3。"""
    def d(a, b):
        return float(np.sqrt((pts[a][0] - pts[b][0]) ** 2 + (pts[a][1] - pts[b][1]) ** 2))
    out = [d(4, 2) / max(d(3, 2), 1e-6)]
    for tip, pip, mcp in ((8, 6, 5), (12, 10, 9), (16, 14, 13), (20, 18, 17)):
        out.append(d(tip, mcp) / max(d(pip, mcp), 1e-6))
    return [min(r, 3.0) for r in out]


def shape10(pose, hands):
    """10 维手形特征（与 JS extractShapeFeatures 同规则）；无骨骼返回 None。"""
    ctx = frame_context(pose)
    if ctx is None:
        return None
    c, sw = ctx
    hl, hr = split_hands(hands, c[0])
    vec = []
    for pts in (hl, hr):
        vec.extend(finger_ratios(pts) if pts is not None else [-1.0] * 5)
    return vec


def feature_rich(pose, hands):
    """146 维富特征：双手 21 点 + 掩码 + 肘腕髋。返回 list 或 None。"""
    ctx = frame_context(pose)
    if ctx is None:
        return None
    c, sw = ctx
    hl, hr = split_hands(hands, c[0])
    vec = []
    for pts in (hl, hr):
        if pts is None:
            vec.extend([0.0] * 42)
        else:
            for p in pts:
                vec.extend(norm_point(p, c, sw))
    vec.append(1.0 if hl is not None else 0.0)
    vec.append(1.0 if hr is not None else 0.0)
    for j in (13, 15, 14, 16, 23, 24):
        p = pose[j] if j < len(pose) else None
        vec.extend(norm_point(p if p is not None else pose[11], c, sw))
    return vec


def downsample(seq, target):
    """线性插值到 target 帧，算法与 matchSLR500() 的关键帧下采样一致。"""
    n = len(seq)
    if n == 0:
        return None
    if n == 1:
        return [list(seq[0]) for _ in range(target)]
    arr = np.asarray(seq, dtype=np.float64)
    out = np.empty((target, arr.shape[1]), dtype=np.float64)
    for i in range(target):
        src = i * (n - 1) / float(target - 1) if target > 1 else 0.0
        lo = int(np.floor(src))
        hi = min(lo + 1, n - 1)
        frac = src - lo
        out[i] = arr[lo] * (1 - frac) + arr[hi] * frac
    return out


def collapse_indices(seq, still_th=0.002, min_keep=10):
    """压缩静止/保持帧，只保留运动帧下标（与 JS collapseIndices 同规则）。"""
    n = len(seq)
    if n == 0:
        return []
    keep = [0]
    last = 0
    for i in range(1, n):
        e = float(np.sqrt(((np.asarray(seq[i], dtype=np.float64) -
                            np.asarray(seq[last], dtype=np.float64)) ** 2).mean()))
        if e > still_th:
            keep.append(i)
            last = i
    if keep[-1] != n - 1:
        keep.append(n - 1)
    if len(keep) < min_keep:
        return [int(round(i * (n - 1) / 9.0)) for i in range(10)]
    return keep


def motion_trim(seq, keep_ratio=0.3, margin=2, min_len=8):
    """按运动能量裁掉首尾静止段。

    阈值基于 80 分位数而非最大值：单个快速动作/抖动尖峰不会把阈值抬高到
    把正常动作全部判成"静止"（旧写法在真实数据上会导致完全不裁剪）。
    同时对能量曲线做 3 帧滑动平均，进一步压掉单帧尖峰。
    若裁完短于 min_len，则依次放宽阈值重试，最终兜底返回原序列。
    """
    n = len(seq)
    if n <= min_len:
        return seq, (0, n)
    arr = np.asarray(seq, dtype=np.float64)
    d = np.abs(np.diff(arr, axis=0)).mean(axis=1)
    if d.size == 0:
        return seq, (0, n)
    if d.size >= 3:
        ds = np.convolve(d, np.ones(3) / 3.0, mode='same')
    else:
        ds = d
    base = float(np.percentile(ds, 80))
    if base <= 0:
        return seq, (0, n)
    for ratio in (keep_ratio, keep_ratio * 0.5, keep_ratio * 0.25):
        thr = base * ratio
        act = np.nonzero(ds > thr)[0]
        if act.size == 0:
            continue
        a = max(0, int(act[0]) - margin)
        b = min(n, int(act[-1]) + 1 + margin)
        if b - a >= min_len:
            return seq[a:b], (a, b)
    return seq, (0, n)


def energies(seq):
    """逐帧运动能量 = 14 维特征的帧间 RMS（与 JS GestureRecognizer.featDist 一致）。"""
    arr = np.asarray(seq, dtype=np.float64)
    e = np.zeros(len(seq), dtype=np.float64)
    for i in range(1, len(seq)):
        e[i] = float(np.sqrt(((arr[i] - arr[i - 1]) ** 2).mean()))
    return e


def median3(e):
    """3 帧中值滤波：单帧检测抖动/丢手造成的能量尖峰不会污染静止判定。"""
    n = len(e)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        lo, hi = max(0, i - 1), min(n, i + 2)
        out[i] = float(np.median(e[lo:hi]))
    return out


def energy_env(seq, ts, win_ms=ENV_MS):
    """运动能量包络：ENV_MS 时间窗内的能量滑动平均（与 JS processSegmentFrame 同口径）。"""
    e = energies(seq)
    n = len(e)
    out = np.zeros(n, dtype=np.float64)
    j = 0
    run = 0.0
    for i in range(n):
        run += e[i]
        while ts[i] - ts[j] > win_ms and j < i:
            run -= e[j]
            j += 1
        out[i] = run / float(i - j + 1)
    return out


def env_threshold(env):
    """包络阈值 = 中位 + ENV_K ×(p95 - 中位)，再抬到不低于中位的 ENV_FLOOR 倍。
    对每条录制自适应，避免固定阈值在不同光照/距离下失效。"""
    med = float(np.median(env))
    p95 = float(np.percentile(env, 95))
    return max(med + ENV_K * (p95 - med), med * ENV_FLOOR, STILL_FLOOR * 0.8)


def _env_segments(env, ts, thr, med, lo, hi, gap_ms, min_ms, hands_n):
    """在 [lo, hi) 内把 env 超阈帧按 gap_ms 分组成运动段，返回 [(a, b), ...]。

    a/b 为含 LEAD_PAD_MS 外扩的模板区间；手检出比例按**核心区间**（超阈帧）统计，
    外扩进来的起势帧往往手还没进画面，不该拉低比例。
    """
    soft = med + 0.10 * max(thr - med, 1e-9)
    above = [i for i in range(lo, hi) if env[i] > thr]
    if not above:
        return []
    groups, cur = [], [above[0]]
    for i in above[1:]:
        if ts[i] - ts[cur[-1]] > gap_ms:
            groups.append(cur)
            cur = [i]
        else:
            cur.append(i)
    groups.append(cur)
    out = []
    for g in groups:
        start, end = g[0], g[-1]
        # 向外扩到包络回落到静止本底为止（把手势的起收斜坡完整纳入模板）
        a, b = start, end + 1
        while a > lo and env[a - 1] > soft and (ts[start] - ts[a - 1]) <= LEAD_PAD_MS:
            a -= 1
        while b < hi and env[b] > soft and (ts[b] - ts[end]) <= LEAD_PAD_MS:
            b += 1
        while (ts[b - 1] - ts[a]) > MAX_SPAN_MS and a < b - 1:
            a += 1
        if (ts[b - 1] - ts[a]) < min_ms:
            continue
        if hands_n is not None:
            core = hands_n[start:end + 1]
            if core:
                tail = core[len(core) * 2 // 3:] or core
                if (sum(1 for v in core if v) < MIN_SPAN_HAND_RATIO * len(core)
                        or sum(1 for v in tail if v) < TAIL_HAND_RATIO * len(tail)):
                    continue    # 手放下/离场造成的身体晃动，不是手势
        out.append((a, b))
    return out


def motion_spans(seq, ts, hands_n=None, still_th=None, max_gap_ms=None, min_span_ms=None):
    """把一次录制切成若干运动段，返回 [(a, b), ...]。

    与 gesture-recognizer.js 的分段口径一致：
      - 用 ENV_MS 能量包络（而非单帧能量）判定运动，压掉检测抖动；
      - 段内允许夹 ≤HOLD_GAP_MS 的保持；段间静止超过 HOLD_GAP_MS 就断成新的一段；
      - 每段两端各外扩 LEAD_PAD_MS，长度夹到 [MIN_SPAN_MS, MAX_SPAN_MS]。
    为什么要多段：一条录制里做了两个明显分开的动作（中间静止 >HOLD_GAP_MS）时，
    两段各自成为一个原型，任一段都能触发该词。
    为什么**不**做"局部阈值再切一遍"：全局阈值会被大幅动作抬高，看似只要按局部统计
    重切就能把小幅动作捞出来（天气第二段 Q 指式峰值 0.023 < 全局阈值 0.045）。
    实测这么做会把"保持期的手部漂移"也切成段（今天 2.25-2.73s，幅度 0.42，与真实动作
    0.47 无法区分），脏原型进模板后会抢匹配、乱出图，代价远大于收益。
    所以：一条录制 = 一个动作；天气的第二段请单独录一条同名词（见 词汇动作描述.md）。
    """
    n = len(seq)
    if n == 0:
        return []
    if ts is None or len(ts) != n:
        dt = 1000.0 / 30.0
        ts = [i * dt for i in range(n)]
    gap_ms = HOLD_GAP_MS if max_gap_ms is None else max_gap_ms
    min_ms = MIN_SPAN_MS if min_span_ms is None else min_span_ms
    env = energy_env(seq, ts)
    med = float(np.median(env))
    thr = env_threshold(env) if still_th is None else still_th
    spans = _env_segments(env, ts, thr, med, 0, n, gap_ms, min_ms, hands_n)

    merged = []
    for a, b in sorted(set(spans)):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged



def motion_span(seq, ts, still_th=None):
    """单段版（旧调用方兼容）：取第一个运动段；整段都没运动则退化为全序列。"""
    n = len(seq)
    if n == 0:
        return 0, 0
    spans = motion_spans(seq, ts, None, still_th)
    if spans:
        return spans[0]
    return 0, n


def cap_len(seq, maxlen=TPL_MAXLEN):
    """序列超过 maxlen 时线性下采样（保持轨迹形状）。"""
    if len(seq) <= maxlen:
        return [list(map(float, v)) for v in seq]
    return [list(map(float, v)) for v in downsample(seq, maxlen)]


def scale_arms(seq, k):
    """幅度增广：把手臂/手的归一化 xy 相对肩中心缩放 k 倍（手掌尺度维不变）。"""
    if k == 1.0:
        return [list(map(float, v)) for v in seq]
    out = []
    for row in seq:
        r = list(map(float, row))
        for d in XY_DIMS:
            r[d] *= k
        out.append(r)
    return out


def descriptor(seq):
    """紧凑描述子 [首帧14, 末帧14, 均值14, 运动幅度1]，用于 DTW 前的廉价预筛。"""
    arr = np.asarray(seq, dtype=np.float64)
    mean = arr.mean(axis=0)
    mag = 0.0
    for i in range(1, len(arr)):
        mag = max(mag, float(np.sqrt(((arr[i] - arr[0]) ** 2).mean())))
    return [round(float(x), 4) for x in list(arr[0]) + list(arr[-1]) + list(mean) + [mag]]


def trim_frac(seq, frac):
    """两端各裁掉 frac 比例，得到时间对齐变体。"""
    L = len(seq)
    k = int(L * frac)
    if L - 2 * k < 8:
        return seq
    return seq[k:L - k]


def kmeans(X, k, iters=25, seed=0):
    """极简 Lloyd k-means（避免引入 sklearn 依赖）。"""
    n = X.shape[0]
    if k <= 1 or n <= k:
        return X.copy()
    rng = np.random.RandomState(seed)
    cent = X[rng.choice(n, k, replace=False)].copy()
    for _ in range(iters):
        d = ((X[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        new = np.empty_like(cent)
        for j in range(k):
            m = lab == j
            new[j] = X[m].mean(axis=0) if m.any() else cent[j]
        if np.allclose(new, cent, atol=1e-9):
            cent = new
            break
        cent = new
    return cent


def parse_name(path):
    """从文件名解析 (词, 说话人, 序号)，规则与 tools/extract_skeleton.py 的 parse_label 一致。

    只有文件名里带说话人标记（s01 / s001 …）时，才认为它遵循数据集命名 `词_说话人_序号`，
    此时**以文件名为准**（录制页的 label 输入框经常被忘掉改，导出后重命名才是真名）。
    不含说话人标记（如录制页默认的 我_20261006_114203.json）时返回 None，交给 JSON 里的 label。
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    toks = [t for t in stem.split('_') if t]
    if not toks:
        return None
    speaker = ''
    for t in toks:
        low = t.lower()
        if len(low) >= 2 and low[0] == 's' and low[1:].isdigit():
            speaker = low
            break
    if not speaker:
        return None
    rest = [t for t in toks[1:] if t.lower() != speaker]
    if toks[0].lower() == speaker:      # 文件名首段就是说话人（如 s01_001.json）→ 没有词，交给 JSON label
        return None
    return toks[0], speaker, (rest[0] if rest else '001')


def path_word(path):
    """文件名第一段（有说话人标记时即词条名）。"""
    nm = parse_name(path)
    if nm:
        return nm[0]
    return os.path.splitext(os.path.basename(path))[0].split('_')[0]


def load_samples(skeleton_dir):
    samples, errors = [], []
    paths = sorted(glob.glob(os.path.join(skeleton_dir, '*.json')))
    # 实时 JS 域骨骼（录制页 V/R 导出的同名 JSON）优先：与识别页同运行时，无域差
    js_paths = sorted(glob.glob(os.path.join('dataset', 'video', '*.json')))
    js_words = set()
    for p in js_paths:
        nm = parse_name(p)
        if nm:
            w = nm[0]
        else:
            w = ''
            try:
                with open(p, 'r', encoding='utf-8') as fh:
                    w = (json.load(fh).get('label') or '').strip()
            except Exception:
                pass
            if w in ('', '未命名'):
                w = path_word(p)
        if w:
            js_words.add(w)
    if js_words:
        before = len(paths)
        paths = [p for p in paths if path_word(p) not in js_words]
        paths = js_paths + paths
        if len(paths) != before:
            print('  提示: 以下词使用实时 JS 域骨骼: %s' % '、'.join(sorted(js_words)))
    for path in paths:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as e:
            errors.append((path, 'JSON 解析失败：%s' % e))
            continue
        if data.get('format') != 'signlanguage-skeleton-v1':
            errors.append((path, '格式不是 signlanguage-skeleton-v1'))
            continue
        frames = data.get('frames') or []
        f18, frich, fshape, hands_n, ts, dropped = [], [], [], [], [], 0
        for f in frames:
            pose, hands = f.get('pose'), f.get('hands')
            a = feature18(pose, hands)
            if a is None:
                dropped += 1
                continue
            f18.append(a)
            frich.append(feature_rich(pose, hands))
            fshape.append(shape10(pose, hands))
            hands_n.append(len([h for h in (hands or []) if len(h.get('pts') or []) >= 21]))
            ts.append(float(f.get('t') or 0.0))
        nm = parse_name(path)
        js_label = (data.get('label') or '').strip()
        if nm:
            label, speaker, index = nm
        else:
            label = js_label if js_label not in ('', '未命名') else path_word(path)
            speaker = data.get('speaker') or 's00'
            index = str(data.get('index') or '')
        samples.append({
            'name': os.path.splitext(os.path.basename(path))[0],
            'path': path,
            'label': label,
            'speaker': speaker,
            'index': index,
            'stats': data.get('stats') or {},
            'source': data.get('source') or {},
            'f18': f18,
            'frich': frich,
            'fshape': fshape,
            'hands_n': hands_n,
            'ts': ts,
            'dropped': dropped,
            'total': len(frames),
        })
    return samples, errors


def main():
    ap = argparse.ArgumentParser(description='骨骼 JSON → 数据集文件（第 2 步）')
    ap.add_argument('--skeleton', default='dataset/skeleton')
    ap.add_argument('--out', default='dataset/processed')
    ap.add_argument('--js', default='自建-templates.js', help='模板 JS 输出路径（项目根目录）')
    ap.add_argument('--proto', type=int, default=3, help='每词原型数（默认 3，对齐 SLR500 多原型版）')
    ap.add_argument('--keyframes', type=int, default=NUM_KEYFRAMES,
                    help='模板关键帧数，必须为 10 才能直接替换现有匹配器')
    ap.add_argument('--rich-length', type=int, default=RICH_LEN_DEFAULT, help='富特征重采样帧数')
    ap.add_argument('--no-trim', action='store_true', help='不做首尾静止段裁剪')
    ap.add_argument('--trim-ratio', type=float, default=0.30,
                    help='运动能量阈值 = 能量曲线80分位 × 该比例（默认 0.30）')
    ap.add_argument('--min-frames', type=int, default=6, help='少于该帧数的样本丢弃')
    ap.add_argument('--test-speaker', default='', help='留作测试集的说话人（如 s02），默认自动挑样本最多的一个')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--dtw-threshold', type=float, default=0.30,
                    help='DTW 归一化距离上限（越小越严格）')
    ap.add_argument('--dtw-ratio', type=float, default=0.32, help='最佳/次佳 词距比值上限')
    ap.add_argument('--strong-dist', type=float, default=0.16,
                    help='强命中距离：低于此值时比值门线性放宽到 --strong-ratio')
    ap.add_argument('--strong-ratio', type=float, default=0.80,
                    help='强命中时允许的比值上限')
    ap.add_argument('--abs-margin', type=float, default=0.08,
                    help='强命中(d≤strong-dist)时，只要领先次佳词这么多绝对距离就放行，'
                         '避免"轨迹很吻合但次佳词也接近"被比值门误杀')
    ap.add_argument('--pre-k', type=int, default=12,
                    help='DTW 前用描述子预筛保留的候选原型数（词表变大时控制耗时）')
    ap.add_argument('--query-max-frames', type=int, default=80,
                    help='实时查询序列最大帧数（超出线性下采样）')
    ap.add_argument('--dtw-shape-cap', type=float, default=0.45, help='手形 DTW 距离上限')
    ap.add_argument('--joint-tol', type=float, default=0.05, help='每维姿态容差（肩宽比例）')
    ap.add_argument('--shape-tol', type=float, default=0.30, help='每维手形容差')
    ap.add_argument('--palm-tol', type=float, default=0.02, help='手掌尺度维（第6/13维）容差')
    ap.add_argument('--dtw-band', type=int, default=12, help='DTW 带状约束额外余量（帧）')
    ap.add_argument('--still-ms', type=int, default=900,
                    help='连续静止多久判定一次手势结束（需 ≥ 动作间保持时长）')
    ap.add_argument('--repeat-lock-ms', type=int, default=700, help='同一手势可重复触发的最小间隔')
    ap.add_argument('--same-perf-gap-ms', type=int, default=1400,
                    help='同一个词两次命中的段间静止小于该值时，视为同一次表演的后续段，不再重复出图')
    ap.add_argument('--cand-void-ms', type=int, default=220,
                    help='连续"在动"多久才作废候选（单帧抖动尖峰不作废）')
    ap.add_argument('--cand-void-rise', type=float, default=0.04,
                    help='作废候选还要求位移比候选形成时大这么多：保持期的手臂缓慢漂移不作废')
    ap.add_argument('--disp-cap', type=float, default=0.018,
                    help='位移运动阈值上限（再高会漏掉"你"这类小幅手势）')
    ap.add_argument('--disp-decay-ms', type=int, default=250,
                    help='位移衰减判定的回看窗口：disp 比该窗口前更低＝手已停住，不算运动')
    ap.add_argument('--settle-env-k', type=float, default=3.0,
                    help='逐帧能量需超过运动阈值这么多倍才刷新 lastMoveT（手举着静止时抖动能量偏大）')
    args = ap.parse_args()

    if args.keyframes != NUM_KEYFRAMES:
        print('⚠ --keyframes=%d 与 gesture-recognizer.js 里写死的 numKeyframes=10 不一致，'
              '生成的模板无法直接替换现有匹配器。' % args.keyframes)

    if not os.path.isdir(args.skeleton):
        os.makedirs(args.skeleton, exist_ok=True)
    if not os.path.isdir(args.out):
        os.makedirs(args.out, exist_ok=True)

    samples, errors = load_samples(args.skeleton)
    for p, msg in errors:
        print('  [跳过] %s：%s' % (os.path.basename(p), msg))
    if not samples:
        print('在 %s 下没有找到可用的骨骼 JSON。' % args.skeleton)
        print('先跑：python3 tools/extract_skeleton.py   （需要 dataset/video/ 下有 MP4）')
        return 1

    # ---- 逐样本：切出运动跨度；同一次录制里的多段动作各自成段（天气 = 转圈 + Q 指式）----
    kept, dropped = [], []
    for s in samples:
        seq = s['f18']
        if len(seq) < args.min_frames:
            dropped.append((s['name'], '有效帧 %d < %d' % (len(seq), args.min_frames)))
            continue
        still_th = None
        if args.no_trim:
            spans = [(0, len(seq))]
        else:
            spans = motion_spans(seq, s.get('ts'), s.get('hands_n'))
            still_th = env_threshold(energy_env(seq, s['ts']))
        if not spans:
            dropped.append((s['name'], '没切出 ≥%dms、收尾仍有手的运动段' % MIN_SPAN_MS))
            continue
        shape_all = [v if v is not None else [-1.0] * SHAPE_DIM for v in s['fshape']]
        multi = len(spans) > 1
        for si, (a, b) in enumerate(spans):
            sub = seq[a:b]
            if len(sub) < args.min_frames:
                dropped.append((s['name'], '第 %d 段运动跨度仅 %d 帧' % (si + 1, len(sub))))
                continue
            e = dict(s)
            if multi:
                e['name'] = '%s#%d' % (s['name'], si + 1)   # 同一次录制的多段，名字要唯一
                e['segment'] = si + 1
            e['segments'] = len(spans)
            e['seq'] = sub
            e['seq_shape'] = shape_all[a:b]
            e['rich'] = np.asarray(downsample(s['frich'][a:b], args.rich_length), dtype=np.float32)
            e['still_th'] = still_th
            e['span_ms'] = (s['ts'][b - 1] - s['ts'][a]) if s.get('ts') and len(s['ts']) == len(seq) else 0.0
            e['kept'] = len(sub)
            kept.append(e)


    if not kept:
        print('没有可用样本（全部被过滤）。丢弃明细：')
        for n, why in dropped:
            print('  - %s：%s' % (n, why))
        return 1

    words = sorted({s['label'] for s in kept})
    speakers = sorted({s['speaker'] for s in kept})

    # ---- DTW 序列模板：每词若干条原型轨迹（保留原始帧序列，不再压成 10 关键帧）----
    seqs = {}
    for w in words:
        members = [s for s in kept if s['label'] == w][:MAX_SEQ_PER_WORD]
        # 样本多时每条样本一个原型；只有一条样本时用 幅度增广 × 时间变体 补足原型数
        scales = AUG_SCALES if len(members) == 1 else (1.0,)
        fracs = TRIM_VARIANTS if len(members) <= 2 else (0.0,)
        protos = []
        for m in members:
            for k in scales:
                for fr in fracs:
                    pseq = cap_len(scale_arms(trim_frac(m['seq'], fr), k))
                    sseq = cap_len(trim_frac(m['seq_shape'], fr))
                    if len(pseq) < 8:
                        continue
                    protos.append({
                        'p': [[round(float(x), 4) for x in row] for row in pseq],
                        's': [[round(float(x), 4) for x in row] for row in sseq],
                        'd': descriptor(pseq),
                        'k': k,
                    })
                    if len(protos) >= MAX_PROTO_PER_WORD:
                        break
                if len(protos) >= MAX_PROTO_PER_WORD:
                    break
            if len(protos) >= MAX_PROTO_PER_WORD:
                break
        if protos:
            seqs[w] = protos

    words = [w for w in words if w in seqs]

    js_path = args.js if os.path.isabs(args.js) else os.path.join('.', args.js)
    with open(js_path, 'w', encoding='utf-8') as fh:
        fh.write('/**\n')
        fh.write(' * 自建手语词库模板（多原型版）\n')
        fh.write(' * 由 tools/build_dataset.py 自动生成于 %s —— 请勿手工编辑\n' % time.strftime('%Y-%m-%d %H:%M:%S'))
        js_src = sorted(glob.glob(os.path.join('dataset', 'video', '*.json')))
        sk_src = sorted(glob.glob(os.path.join(args.skeleton, '*.json')))
        fh.write(' * 共 %d 个词汇，每词最多 %d 个原型（本次 %d 个样本）\n'
                 % (len(words), MAX_PROTO_PER_WORD, len(kept)))
        fh.write(' * 每个原型: {p: 14维姿态序列, s: 10维手形序列, d: 43维紧凑描述子}\n')
        fh.write(' *   序列 = 录制里的"运动跨度"（含手势内部 ≤%dms 的保持），超过 %d 帧则线性下采样\n'
                 % (HOLD_GAP_MS, TPL_MAXLEN))
        fh.write(' * 特征关节顺序: ElbowL, WristL, HandL, palmL, ElbowR, WristR, HandR, palmR\n')
        fh.write(' * 归一化: 以双肩中点为中心, 肩宽为缩放因子, x/y 取负以对齐 Kinect 坐标系\n')
        fh.write(' * 匹配: 子序列 DTW（带斜率约束）+ 43 维描述子预筛，与 gesture-recognizer.js 同口径\n')
        fh.write(' * 数据来源: dataset/video/*.json（录制页导出的 JS 域骨骼，%d 个文件，优先）' % len(js_src))
        if sk_src:
            fh.write('；dataset/skeleton/*.json（%d 个文件，同名词汇以 video 为准）' % len(sk_src))
        fh.write('\n')
        fh.write(' */\n\n')
        fh.write('const CUSTOM_WORDS = %s;\n\n' % json.dumps(words, ensure_ascii=False))
        fh.write('// DTW 序列原型: {词: [{p:[[14维]×帧], s:[[10维]×帧], d:[首14,末14,均14,幅度1]}, ...]}\n')
        fh.write('const CUSTOM_SEQS = {\n')
        for i, w in enumerate(words):
            rows = ',\n    '.join(
                '{p:[' + ','.join('[' + ','.join('%.4f' % v for v in fr) + ']' for fr in pr['p']) + '],'
                's:[' + ','.join('[' + ','.join('%.4f' % v for v in fr) + ']' for fr in pr['s']) + '],'
                'd:[' + ','.join('%.4f' % v for v in pr['d']) + ']}'
                for pr in seqs[w])
            fh.write('  %s: [\n    %s\n  ]%s\n'
                     % (json.dumps(w, ensure_ascii=False), rows, ',' if i < len(words) - 1 else ''))
        fh.write('};\n')
        fh.write('\n// 匹配参数（与 gesture-recognizer.js 的 DTW 门控一致）\n')
        fh.write('const CUSTOM_DTW = %s;\n' % json.dumps({
            'threshold': args.dtw_threshold, 'ratio': args.dtw_ratio,
            'shapeCap': args.dtw_shape_cap, 'jointTol': args.joint_tol,
            'shapeTol': args.shape_tol, 'shapeW': SHAPE_WEIGHT,
            'band': args.dtw_band, 'maxLen': TPL_MAXLEN,
            'stillMs': args.still_ms, 'holdGapMs': HOLD_GAP_MS,
            'leadPadMs': LEAD_PAD_MS, 'minSpanMs': MIN_SPAN_MS,
            'maxSpanMs': MAX_SPAN_MS, 'stillFloor': STILL_FLOOR,
            'stillCeil': STILL_CEIL, 'repeatLockMs': args.repeat_lock_ms,
            'samePerfGapMs': args.same_perf_gap_ms,
            'strongDist': args.strong_dist, 'strongRatio': args.strong_ratio,
            'absMargin': args.abs_margin,
            'palmTol': args.palm_tol, 'queryMaxFrames': args.query_max_frames,
            'preK': args.pre_k,
            'candVoidMs': args.cand_void_ms, 'candVoidRise': args.cand_void_rise,
            'dispCap': args.disp_cap,
            'dispDecayMs': args.disp_decay_ms, 'settleEnvK': args.settle_env_k,
            'minSpanHandRatio': MIN_SPAN_HAND_RATIO, 'tailHandRatio': TAIL_HAND_RATIO,
        }, ensure_ascii=False))
        fh.write('\n// 兼容旧变量名（已停用 10 关键帧匹配路径）\n')
        fh.write('const CUSTOM_SHAPE_WEIGHT = %s;\n' % SHAPE_WEIGHT)

    # ---- 富特征 npz ----
    X = np.stack([s['rich'] for s in kept]).astype(np.float32)
    y = np.array([words.index(s['label']) for s in kept], dtype=np.int16)
    sp = np.array([s['speaker'] for s in kept])
    names = np.array([s['name'] for s in kept])
    npz_path = os.path.join(args.out, 'dataset.npz')
    np.savez_compressed(npz_path, X=X, y=y, speakers=sp, names=names,
                        words=np.array(words))

    # ---- 划分（按说话人，避免数据泄漏）----
    test_sp = args.test_speaker
    if not test_sp and len(speakers) >= 2:
        cnt = {s: sum(1 for k in kept if k['speaker'] == s) for s in speakers}
        test_sp = min(cnt, key=lambda s: cnt[s])
    split = {'mode': 'by-speaker' if test_sp else 'single-speaker(仅留一法)',
             'testSpeaker': test_sp or None,
             'train': [s['name'] for s in kept if not test_sp or s['speaker'] != test_sp],
             'test': [s['name'] for s in kept if test_sp and s['speaker'] == test_sp]}

    # ---- labels.json ----
    per_word = {}
    for s in kept:
        d = per_word.setdefault(s['label'], {'count': 0, 'files': set(), 'speakers': set(),
                                             'frames': [], 'dropRate': []})
        d['count'] += 1
        d['files'].add(s['name'].split('#')[0])
        d['speakers'].add(s['speaker'])
        d['frames'].append(s['kept'])
        if s['total']:
            d['dropRate'].append(round(s['dropped'] / float(s['total']), 3))
    labels_meta = {
        'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'format': 'signlanguage-skeleton-v1',
        'words': words,
        'speakers': speakers,
        'sampleCount': len(kept),
        'droppedCount': len(dropped),
        'template': {'file': os.path.basename(js_path), 'keyframes': args.keyframes,
                     'featureDim': NUM_FEATURES, 'flatDim': args.keyframes * NUM_FEATURES,
                     'prototypes': args.proto,
                     'compatibleWith': 'gesture-recognizer.js extractPoseFeatures()/matchSLR500()'},
        'rich': {'file': 'dataset.npz', 'shape': list(X.shape), 'length': args.rich_length,
                 'dim': RICH_DIM,
                 'layout': 'L21*3 + R21*3 + maskL + maskR + [ElbowL,WristL,ElbowR,WristR,HipL,HipR]*3'},
        'split': split,
        'perWord': {w: {'count': per_word[w]['count'],
                        'speakers': sorted(per_word[w]['speakers']),
                        'segments': per_word[w]['count'],
                        'recordings': len(per_word[w]['files']),
                        'framesMin': int(min(per_word[w]['frames'])),
                        'framesMax': int(max(per_word[w]['frames'])),
                        'framesAvg': round(float(np.mean(per_word[w]['frames'])), 1),
                        'poseDropRate': round(float(np.mean(per_word[w]['dropRate'])), 3) if per_word[w]['dropRate'] else None}
                    for w in words},
    }
    with open(os.path.join(args.out, 'labels.json'), 'w', encoding='utf-8') as fh:
        json.dump(labels_meta, fh, ensure_ascii=False, indent=2)

    # ---- report.md ----
    lines = ['# 自建数据集采集报告', '',
             '> 由 `tools/build_dataset.py` 生成于 %s' % time.strftime('%Y-%m-%d %H:%M:%S'), '',
             '## 总览', '',
             '- 词条数：**%d**' % len(words),
             '- 说话人数：**%d**（%s）' % (len(speakers), '、'.join(speakers)),
             '- 有效样本：**%d**，被过滤：%d' % (len(kept), len(dropped)),
             '- 模板：%s（%d 词 × ≤%d 原型 × %d 关键帧 × %d 维）'
             % (os.path.basename(js_path), len(words), args.proto, args.keyframes, NUM_FEATURES),
             '- 富特征：dataset.npz，形状 %s' % (list(X.shape),),
             '- 划分：%s%s' % (split['mode'], ('，测试说话人 = %s' % test_sp) if test_sp else ''),
             '', '## 每词明细', '',
             '| 词 | 运动段 | 录制 | 说话人 | 有效帧(最小/均值/最大) | 无骨骼帧占比 | 评价 |',
             '|---|---|---|---|---|---|---|']
    for w in words:
        d = per_word[w]
        avg = round(float(np.mean(d['frames'])), 1)
        dr = round(float(np.mean(d['dropRate'])), 3) if d['dropRate'] else 0.0
        verdict = 'ok'
        if len(d['files']) < 3:
            verdict = '⚠ 样本过少（<3），模板原型不足'
        elif len(d['files']) < 8:
            verdict = '△ 建议补到 8 条以上'
        if dr > 0.2:
            verdict += '；⚠ %.0f%% 帧无身体骨骼' % (dr * 100)
        lines.append('| %s | %d | %d | %s | %d / %s / %d | %.1f%% | %s |'
                     % (w, d['count'], len(d['files']), '、'.join(sorted(d['speakers'])),
                        int(min(d['frames'])), avg, int(max(d['frames'])), dr * 100, verdict))
    if dropped:
        lines += ['', '## 被过滤的样本', '']
        for n, why in dropped:
            lines.append('- `%s`：%s' % (n, why))
    lines += ['', '## 下一步', '',
              '1. 补齐上表中标 ⚠ / △ 的词条样本；',
              '2. 跑 leave-one-out 评估（`tools/eval_dtw.py`，待实现），目标 top-1 ≥ 90%；',
              '3. 前端已接入：`index.html` 直接加载 `%s`，`matchSLR500()` 的 `useCustom` 分支只用 '
              '`CUSTOM_WORDS/CUSTOM_SEQS/CUSTOM_DTW`（SLR500 旧链路已删除），重跑本脚本后刷新页面即可生效。'
              % os.path.basename(js_path), '']
    with open(os.path.join(args.out, 'report.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))

    print('\n=== 构建完成 ===')
    print('词条 %d 个：%s' % (len(words), ' '.join(words)))
    print('样本 %d 条（过滤 %d 条），说话人 %s' % (len(kept), len(dropped), '、'.join(speakers)))
    print('输出：')
    print('  - %s                       （%d 词 × ≤%d 原型 × %d 维，index.html 直接加载）'
          % (js_path, len(words), args.proto, args.keyframes * NUM_FEATURES))
    print('  - %s        X%s' % (npz_path, list(X.shape)))
    print('  - %s' % os.path.join(args.out, 'labels.json'))
    print('  - %s' % os.path.join(args.out, 'report.md'))
    thin = [w for w in words if len(per_word[w]['files']) < 3]
    if thin:
        print('\n⚠ 样本少于 3 条的词（原型不足，识别会不稳）：%s' % '、'.join(thin))
    return 0


if __name__ == '__main__':
    sys.exit(main())
