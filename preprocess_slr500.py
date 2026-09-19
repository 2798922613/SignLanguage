#!/usr/bin/env python3
"""
SLR500 手语骨骼数据预处理脚本（多原型版）
从 slr500_words_joints/ 提取归一化骨骼原型，输出为 slr500-templates.js

为什么用"多原型"而不是"单均值"：
    50 个人做同一个词的节奏/轨迹差异很大，直接对 250 个样本求平均会把时间结构糊掉，
    离线自测 top-1 只有 ~54%。改为每个词保留 3 个"跨受试者均匀分布"的真实样本作为原型，
    匹配时取 min 距离，leave-one-out top-1 提升到 ~79%。

数据格式：
- 每个 _body.txt：每行75个浮点数 = 25个Kinect关节 × 3D坐标(x,y,z)
- 特征关节：ElbowL(5) WristL(6) HandL(7) ElbowR(9) WristR(10) HandR(11)
- 归一化：以 SpineShoulder(20) 为中心，肩宽(4-8)为缩放因子
- 每个原型：10 关键帧 × 18 特征 = 180 值；每词 3 个原型
"""

import os
import json
import math

DATA_DIR = 'slr500_words_joints'
OUTPUT_FILE = 'slr500-templates.js'
NUM_KEYFRAMES = 10
NUM_PROTOTYPES = 3

FEATURE_JOINTS = [5, 6, 7, 9, 10, 11]
CENTER_JOINT = 20
SHOULDER_L = 4
SHOULDER_R = 8
NUM_FEATURES_PER_FRAME = len(FEATURE_JOINTS) * 3  # 18


def load_dictionary():
    dictionary = {}
    with open(os.path.join(DATA_DIR, 'dictionary.txt'), 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                word = parts[1]
                if '（' in word:
                    word = word.split('（')[0]
                dictionary[int(parts[0])] = word
    return dictionary


def parse_body_file(filepath):
    frames = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            values = []
            for p in line.split('\t'):
                p = p.strip()
                if p:
                    try:
                        values.append(float(p))
                    except ValueError:
                        values.append(0.0)
            if len(values) >= 75:
                frames.append([tuple(values[i*3:i*3+3]) for i in range(25)])
    return frames


def normalize_frame(joints):
    cx, cy, cz = joints[CENTER_JOINT]
    sl = joints[SHOULDER_L]
    sr = joints[SHOULDER_R]
    sw = math.sqrt((sl[0]-sr[0])**2 + (sl[1]-sr[1])**2 + (sl[2]-sr[2])**2)
    if sw < 0.01:
        sw = 0.3
    feats = []
    for ji in FEATURE_JOINTS:
        jx, jy, jz = joints[ji]
        feats.extend([round((jx-cx)/sw, 4), round((jy-cy)/sw, 4), round((jz-cz)/sw, 4)])
    return feats


def downsample(frames_features, target):
    n = len(frames_features)
    if n == 0:
        return [0.0] * (target * NUM_FEATURES_PER_FRAME)
    if n == 1:
        return frames_features[0] * target
    out = []
    for i in range(target):
        s = i * (n - 1) / (target - 1) if target > 1 else 0
        lo = int(s)
        hi = min(lo + 1, n - 1)
        f = s - lo
        low = frames_features[lo]
        high = frames_features[hi]
        out.extend([round(low[j]*(1-f) + high[j]*f, 4) for j in range(len(low))])
    return out


def sample_to_prototype(filepath):
    frames = parse_body_file(filepath)
    if len(frames) < 3:
        return None
    return downsample([normalize_frame(j) for j in frames], NUM_KEYFRAMES)


def process_word(word_dir):
    files = sorted(f for f in os.listdir(word_dir) if f.endswith('._body.txt'))
    if not files:
        return None
    # 跨受试者均匀取 NUM_PROTOTYPES 个样本作为原型（文件按 P01..P50 排序）
    idx = [round(i * (len(files) - 1) / (NUM_PROTOTYPES - 1)) for i in range(NUM_PROTOTYPES)]
    prototypes = []
    for ii in idx:
        proto = sample_to_prototype(os.path.join(word_dir, files[ii]))
        if proto:
            prototypes.append(proto)
    return prototypes or None


def main():
    print("正在加载词典...")
    dictionary = load_dictionary()
    print(f"共 {len(dictionary)} 个词汇")

    templates = {}
    for word_id in range(500):
        word_dir = os.path.join(DATA_DIR, f'{word_id:03d}')
        if not os.path.isdir(word_dir):
            continue
        word = dictionary.get(word_id, f'word_{word_id:03d}')
        protos = process_word(word_dir)
        if protos:
            templates[word] = protos
        if (word_id + 1) % 100 == 0:
            print(f"  已处理 {word_id + 1}/500...")

    print(f"\n成功生成 {len(templates)} 个词汇 × {NUM_PROTOTYPES} 原型")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('/**\n')
        f.write(' * SLR500 手语词汇模板数据（多原型版）\n')
        f.write(' * 由 preprocess_slr500.py 自动生成\n')
        f.write(f' * 共 {len(templates)} 个词汇，每词 {NUM_PROTOTYPES} 个原型\n')
        f.write(f' * 每个原型: {NUM_KEYFRAMES} 关键帧 × {NUM_FEATURES_PER_FRAME} 特征值\n')
        f.write(' * 特征关节顺序: ElbowL, WristL, HandL, ElbowR, WristR, HandR\n')
        f.write(' * 归一化: 以SpineShoulder为中心, 肩宽为缩放因子\n')
        f.write(' * 匹配: 对每词的多个原型取最小RMS距离\n')
        f.write(' */\n\n')
        f.write('const SLR500_WORDS = ')
        f.write(json.dumps(list(templates.keys()), ensure_ascii=False))
        f.write(';\n\n')
        f.write('const SLR500_TEMPLATES = ')
        f.write(json.dumps(templates, ensure_ascii=False, separators=(',', ':')))
        f.write(';\n')

    size = os.path.getsize(OUTPUT_FILE)
    print(f"输出文件: {OUTPUT_FILE} ({size/1024:.1f} KB)")


if __name__ == '__main__':
    main()
