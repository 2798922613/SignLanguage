#!/usr/bin/env python3
"""
SLR500 手语骨骼数据预处理脚本
从 slr500_words_joints/ 提取归一化模板特征，输出为 slr500-templates.js
用于前端实时手语识别的模板匹配

数据格式：
- 每个 _body.txt 文件：每行75个浮点数 = 25个Kinect关节 × 3D坐标(x,y,z)
- Kinect 25关节：
  0:SpineBase 1:SpineMid 2:Neck 3:Head
  4:ShoulderLeft 5:ElbowLeft 6:WristLeft 7:HandLeft
  8:ShoulderRight 9:ElbowRight 10:WristRight 11:HandRight
  12:HipLeft 13:KneeLeft 14:AnkleLeft 15:FootLeft
  16:HipRight 17:KneeRight 18:AnkleRight 19:FootRight
  20:SpineShoulder 21:HandTipLeft 22:ThumbLeft 23:HandTipRight 24:ThumbRight
"""

import os
import json
import math

DATA_DIR = 'slr500_words_joints'
OUTPUT_FILE = 'slr500-templates.js'
NUM_KEYFRAMES = 10

# 用于特征的关节索引（手臂和手部相关）
FEATURE_JOINTS = [5, 6, 7, 9, 10, 11]  # ElbowL, WristL, HandL, ElbowR, WristR, HandR
CENTER_JOINT = 20  # SpineShoulder 作为身体中心
SHOULDER_L = 4
SHOULDER_R = 8
NUM_FEATURES_PER_FRAME = len(FEATURE_JOINTS) * 3  # 18

def load_dictionary():
    """加载词典映射"""
    dictionary = {}
    with open(os.path.join(DATA_DIR, 'dictionary.txt'), 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                word_id = int(parts[0])
                word = parts[1]
                # 去掉括号中的别名，保留主词
                if '（' in word:
                    word = word.split('（')[0]
                dictionary[word_id] = word
    return dictionary

def parse_body_file(filepath):
    """解析单个骨骼数据文件，返回帧列表"""
    frames = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            values = []
            for p in parts:
                p = p.strip()
                if p:
                    try:
                        values.append(float(p))
                    except ValueError:
                        values.append(0.0)
            if len(values) >= 75:
                # 取前75个值 = 25关节 × 3坐标
                joints = []
                for i in range(25):
                    x = values[i * 3]
                    y = values[i * 3 + 1]
                    z = values[i * 3 + 2]
                    joints.append((x, y, z))
                frames.append(joints)
    return frames

def normalize_frame(joints):
    """
    归一化单帧数据：
    - 以SpineShoulder为中心
    - 以肩宽为缩放因子
    - 提取特征关节
    """
    # 身体中心
    cx, cy, cz = joints[CENTER_JOINT]
    
    # 肩宽
    sl = joints[SHOULDER_L]
    sr = joints[SHOULDER_R]
    shoulder_width = math.sqrt(
        (sl[0] - sr[0])**2 + (sl[1] - sr[1])**2 + (sl[2] - sr[2])**2
    )
    if shoulder_width < 0.01:
        shoulder_width = 0.3  # 默认肩宽
    
    # 提取并归一化特征关节
    features = []
    for ji in FEATURE_JOINTS:
        jx, jy, jz = joints[ji]
        nx = (jx - cx) / shoulder_width
        ny = (jy - cy) / shoulder_width
        nz = (jz - cz) / shoulder_width
        features.extend([round(nx, 4), round(ny, 4), round(nz, 4)])
    
    return features

def downsample_sequence(frames_features, target_length):
    """将帧序列下采样到目标长度"""
    n = len(frames_features)
    if n == 0:
        return [0.0] * (target_length * NUM_FEATURES_PER_FRAME)
    if n == 1:
        return frames_features[0] * target_length
    
    result = []
    for i in range(target_length):
        # 线性插值索引
        src_idx = i * (n - 1) / (target_length - 1) if target_length > 1 else 0
        idx_low = int(src_idx)
        idx_high = min(idx_low + 1, n - 1)
        frac = src_idx - idx_low
        
        # 插值
        low = frames_features[idx_low]
        high = frames_features[idx_high]
        interpolated = []
        for j in range(len(low)):
            val = low[j] * (1 - frac) + high[j] * frac
            interpolated.append(round(val, 4))
        result.extend(interpolated)
    
    return result

def process_word(word_dir, word_id):
    """处理单个词汇的所有样本"""
    all_sequences = []
    
    files = sorted([f for f in os.listdir(word_dir) if f.endswith('._body.txt')])
    
    for filename in files:
        filepath = os.path.join(word_dir, filename)
        frames = parse_body_file(filepath)
        
        if len(frames) < 3:
            continue
        
        # 归一化每一帧
        frames_features = []
        for frame_joints in frames:
            features = normalize_frame(frame_joints)
            frames_features.append(features)
        
        # 下采样到固定长度
        sequence = downsample_sequence(frames_features, NUM_KEYFRAMES)
        all_sequences.append(sequence)
    
    if not all_sequences:
        return None
    
    # 对所有样本求平均
    seq_len = len(all_sequences[0])
    avg_template = [0.0] * seq_len
    for seq in all_sequences:
        for i in range(seq_len):
            avg_template[i] += seq[i]
    
    for i in range(seq_len):
        avg_template[i] = round(avg_template[i] / len(all_sequences), 4)
    
    return avg_template

def main():
    print("正在加载词典...")
    dictionary = load_dictionary()
    print(f"共 {len(dictionary)} 个词汇")
    
    templates = {}
    word_info = []
    
    for word_id in range(500):
        word_dir = os.path.join(DATA_DIR, f'{word_id:03d}')
        if not os.path.isdir(word_dir):
            print(f"  跳过 {word_id:03d}: 目录不存在")
            continue
        
        word = dictionary.get(word_id, f'word_{word_id:03d}')
        template = process_word(word_dir, word_id)
        
        if template is not None:
            templates[word] = template
            word_info.append({'id': word_id, 'word': word, 'samples': 1})
            
            if (word_id + 1) % 50 == 0:
                print(f"  已处理 {word_id + 1}/500...")
    
    print(f"\n成功生成 {len(templates)} 个词汇模板")
    print(f"每个模板: {NUM_KEYFRAMES} 关键帧 × {NUM_FEATURES_PER_FRAME} 特征 = {NUM_KEYFRAMES * NUM_FEATURES_PER_FRAME} 值")
    
    # 输出为JS文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('/**\n')
        f.write(' * SLR500 手语词汇模板数据\n')
        f.write(' * 由 preprocess_slr500.py 自动生成\n')
        f.write(f' * 共 {len(templates)} 个词汇\n')
        f.write(f' * 每个模板: {NUM_KEYFRAMES} 关键帧 × {NUM_FEATURES_PER_FRAME} 特征值\n')
        f.write(' *\n')
        f.write(' * 特征关节顺序: ElbowL, WristL, HandL, ElbowR, WristR, HandR\n')
        f.write(' * 归一化: 以SpineShoulder为中心, 肩宽为缩放因子\n')
        f.write(' */\n\n')
        
        # 词汇列表（有序）
        words_list = list(templates.keys())
        f.write('const SLR500_WORDS = ')
        f.write(json.dumps(words_list, ensure_ascii=False))
        f.write(';\n\n')
        
        # 模板数据
        f.write('const SLR500_TEMPLATES = ')
        json_str = json.dumps(templates, ensure_ascii=False, separators=(',', ':'))
        f.write(json_str)
        f.write(';\n')
    
    file_size = os.path.getsize(OUTPUT_FILE)
    print(f"\n输出文件: {OUTPUT_FILE} ({file_size / 1024:.1f} KB)")
    
    # 输出统计
    print(f"\n前20个词汇:")
    for i, (word, template) in enumerate(list(templates.items())[:20]):
        print(f"  {i+1}. {word} ({len(template)} 特征值)")

if __name__ == '__main__':
    main()
