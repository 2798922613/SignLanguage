#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MP4 → 骨骼序列 JSON（自建手语数据集 · 第 1 步）

输入：dataset/video/ 下的视频文件；**文件名或所在子文件夹名即词条标签**
输出：dataset/skeleton/<词>_<说话人>_<序号>.json，格式 signlanguage-skeleton-v1
      与 skeleton-recorder.html 浏览器内导出的 JSON 完全同构，两者可混用。

为什么用离线提取，而不是在浏览器里一段段录：
  - 一次跑完整个文件夹，不用逐个手动开录/停录；
  - MediaPipe Python 与前端用的是同一套模型和同一套坐标约定，结果可复现、可重跑；
  - 可顺手输出「骨骼叠加预览视频」，用肉眼校验骨骼是否贴合（--overlay）。

命名规则（两种都支持，二选一即可）：
  A. 平铺：  dataset/video/今天_s01_003.mp4   → 词=今天  说话人=s01  序号=003
             dataset/video/今天.mp4           → 词=今天  说话人=s00  序号=001
             dataset/video/今天_003.mp4       → 词=今天  说话人=s00  序号=003
  B. 分文件夹：dataset/video/今天/s01_003.mp4 → 词=今天(取文件夹名) 说话人=s01 序号=003

常用命令：
    python3 tools/extract_skeleton.py                    # 提取 dataset/video 下全部视频
    python3 tools/extract_skeleton.py --list             # 只看命名解析结果，不做处理
    python3 tools/extract_skeleton.py --overlay          # 额外生成骨骼叠加预览 mp4
    python3 tools/extract_skeleton.py --only 今天 --force # 只重跑某个词，忽略已有结果
    python3 tools/extract_skeleton.py --rotate 90        # 手机竖拍需顺时针转 90° 时用
"""

import argparse
import json
import os
import sys
import time

# 必须在 import mediapipe / cv2 之前设置，否则日志会淹没输出
os.environ.setdefault('GLOG_minloglevel', '2')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
os.environ.setdefault('ABSL_MIN_LOG_LEVEL', '2')
os.environ.setdefault('MPLCONFIGDIR', '/tmp/mplconfig')

import cv2                      # noqa: E402
import numpy as np              # noqa: E402

VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.webm', '.avi', '.mkv')
FORMAT_NAME = 'signlanguage-skeleton-v1'

# MediaPipe Pose 33 点连接关系（与 gesture-recognizer.js / skeleton-recorder.html 一致）
POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7),
    (0, 4), (4, 5), (5, 6), (6, 8),
    (9, 10),
    (11, 12), (11, 23), (12, 24), (23, 24),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (15, 17), (15, 19), (17, 19), (19, 21),
    (16, 18), (16, 20), (18, 20), (20, 22),
    (23, 25), (25, 27), (27, 29), (27, 31), (29, 31),
    (24, 26), (26, 28), (28, 30), (28, 32), (30, 32),
]

# 预览绘制用（BGR），配色与前端一致
HAND_FINGERS = [
    ((255, 255, 255), [(0, 17)]),
    ((107, 107, 255), [(0, 1), (1, 2), (2, 3), (3, 4)]),
    ((61, 217, 255), [(0, 5), (5, 6), (6, 7), (7, 8)]),
    ((119, 203, 107), [(5, 9), (9, 10), (10, 11), (11, 12)]),
    ((255, 150, 77), [(9, 13), (13, 14), (14, 15), (15, 16)]),
    ((250, 128, 199), [(13, 17), (17, 18), (18, 19), (19, 20)]),
]
POSE_COLOR = (255, 191, 0)      # #00BFFF -> BGR
POSE_DOT = (180, 105, 255)      # #FF69B4 -> BGR
POSE_VIS_MIN = 0.35

COORD_SPEC = {
    'space': '归一化坐标，原点为原始（未镜像、未旋转前请先看 source.rotate）视频帧左上角，'
             'x 向右、y 向下，范围 [0,1]；z 为相对深度，负值更靠近摄像头',
    'hands': 'MediaPipe Hands 21 点/手，索引与 HAND_CONNECTIONS 一致；label 为 MediaPipe 原始左右手标签',
    'pose': 'MediaPipe Pose 33 点，第 4 个分量为 visibility；pose 为 null 表示该帧未检测到人',
    'units': 't 为该帧在原视频时间轴上的毫秒数（按原始帧号/原始帧率计算）',
}


def r4(v):
    return round(float(v or 0.0), 4)


def r3(v):
    return round(float(v if v is not None else 1.0), 3)


def parse_label(path, src_root):
    """从路径推断 词/说话人/序号，支持平铺与子文件夹两种命名。"""
    rel = os.path.relpath(path, src_root)
    parts = rel.replace(os.sep, '/').split('/')
    stem = os.path.splitext(parts[-1])[0]
    tokens = [t for t in stem.split('_') if t]

    def pick_speaker(toks):
        for t in toks:
            low = t.lower()
            if len(low) >= 2 and low[0] == 's' and low[1:].isdigit():
                return low
        return ''

    if len(parts) >= 2:
        label = parts[0]                       # 子文件夹名即词
        speaker = pick_speaker(tokens)
        rest = [t for t in tokens if t.lower() != speaker]
        idx = '_'.join(rest) if rest else '001'
    else:
        label = tokens[0] if tokens else stem
        speaker = pick_speaker(tokens[1:])
        rest = [t for t in tokens[1:] if t.lower() != speaker]
        idx = '_'.join(rest) if rest else '001'
    return (label or stem), (speaker or 's00'), (idx or '001')


def find_videos(src_root):
    out = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.startswith('.'):
                continue
            if fn.lower().endswith(VIDEO_EXTS):
                out.append(os.path.join(dirpath, fn))
    return out


def rotate_frame(frame, deg):
    deg = int(deg) % 360
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def hand_records(res):
    out = []
    if not res.multi_hand_landmarks:
        return out
    handed = res.multi_handedness or []
    for i, hl in enumerate(res.multi_hand_landmarks):
        label, score = None, None
        if i < len(handed) and handed[i].classification:
            c = handed[i].classification[0]
            label, score = c.label, r3(c.score)
        out.append({
            'label': label,
            'score': score,
            'pts': [[r4(p.x), r4(p.y), r4(p.z)] for p in hl.landmark],
        })
    return out


def pose_record(res):
    if res.pose_landmarks is None:
        return None
    return [[r4(p.x), r4(p.y), r4(p.z), r3(p.visibility)] for p in res.pose_landmarks.landmark]


def draw_preview(frame, hands, pose, text):
    h, w = frame.shape[:2]
    u = max(1.0, w / 640.0)
    if pose:
        for a, b in POSE_CONNECTIONS:
            if a >= len(pose) or b >= len(pose):
                continue
            pa, pb = pose[a], pose[b]
            v = min(pa[3], pb[3])
            if v < POSE_VIS_MIN:
                continue
            col = tuple(int(c * (0.35 + 0.65 * v)) for c in POSE_COLOR)
            cv2.line(frame, (int(pa[0] * w), int(pa[1] * h)), (int(pb[0] * w), int(pb[1] * h)),
                     col, max(1, int(round(2.2 * u))))
        for j, p in enumerate(pose):
            if p[3] < POSE_VIS_MIN:
                continue
            cv2.circle(frame, (int(p[0] * w), int(p[1] * h)),
                       max(1, int(round((1.6 if j <= 10 else 2.8) * u))), POSE_DOT, -1)
    for hand in hands:
        pts = hand['pts']
        for col, links in HAND_FINGERS:
            for a, b in links:
                cv2.line(frame, (int(pts[a][0] * w), int(pts[a][1] * h)),
                         (int(pts[b][0] * w), int(pts[b][1] * h)), col, max(1, int(round(2.4 * u))))
        for i, p in enumerate(pts):
            cv2.circle(frame, (int(p[0] * w), int(p[1] * h)),
                       max(2, int(round(2.6 * u))), (255, 255, 255), -1)
            cv2.circle(frame, (int(p[0] * w), int(p[1] * h)),
                       max(1, int(round(1.5 * u))), (49, 49, 255), -1)
    if text:
        cv2.rectangle(frame, (0, 0), (w, int(26 * u)), (0, 0, 0), -1)
        cv2.putText(frame, text, (int(6 * u), int(18 * u)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * u, (255, 255, 255), max(1, int(u)), cv2.LINE_AA)
    return frame


def process_video(path, args, src_root, skeleton_dir, preview_dir):
    import mediapipe as mp

    label, speaker, idx = parse_label(path, src_root)
    if args.only and label != args.only:
        return None
    rel = os.path.relpath(path, src_root).replace(os.sep, '/')
    out_name = '%s_%s_%s' % (label, speaker, idx)
    out_path = os.path.join(skeleton_dir, out_name + '.json')
    if os.path.exists(out_path) and not args.force:
        if os.path.getmtime(out_path) >= os.path.getmtime(path):
            print('  [跳过] %s（已有结果，加 --force 可重跑）' % rel)
            return {'label': label, 'skipped': True}

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print('  [失败] 无法打开视频：%s' % rel)
        return {'label': label, 'error': 'open failed'}

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if src_fps <= 1 or src_fps > 240:
        src_fps = 30.0
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    rotate = args.rotate
    if str(rotate) == 'auto':
        meta = 0
        if hasattr(cv2, 'CAP_PROP_ORIENTATION_META'):
            try:
                meta = int(cap.get(cv2.CAP_PROP_ORIENTATION_META) or 0)
            except Exception:
                meta = 0
        rotate = meta if meta in (0, 90, 180, 270) else 0
    rotate = int(rotate)
    stride = max(1, int(round(src_fps / args.fps))) if args.fps > 0 else 1
    sampled_fps = src_fps / stride

    hands = mp.solutions.hands.Hands(
        static_image_mode=False, max_num_hands=args.max_hands,
        model_complexity=args.hand_complexity,
        min_detection_confidence=args.min_hand_conf,
        min_tracking_confidence=args.min_track_conf)
    pose = None
    if not args.no_pose:
        pose = mp.solutions.pose.Pose(
            static_image_mode=False, model_complexity=args.pose_complexity,
            smooth_landmarks=True, enable_segmentation=False,
            min_detection_confidence=args.min_pose_conf,
            min_tracking_confidence=args.min_pose_conf)

    writer = None
    if args.overlay:
        prev_path = os.path.join(preview_dir, out_name + '.mp4')
        # 尺寸要等第一帧才知道，延后创建

    frames = []
    fi = 0
    kept = 0
    hands_hit = 0
    pose_hit = 0
    w = h = 0
    t_start = time.time()
    max_frames = args.max_frames if args.max_frames > 0 else 10 ** 9

    try:
        while kept < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            cur = fi
            fi += 1
            if cur % stride:
                continue
            if rotate:
                frame = rotate_frame(frame, rotate)
            if args.mirror:
                frame = cv2.flip(frame, 1)
            if h == 0:
                h, w = frame.shape[:2]
                if args.overlay:
                    prev_path = os.path.join(preview_dir, out_name + '.mp4')
                    writer = cv2.VideoWriter(prev_path, cv2.VideoWriter_fourcc(*'mp4v'),
                                             max(1.0, sampled_fps), (w, h))
                    if not writer.isOpened():
                        print('  [提示] 预览视频编码器不可用，已跳过 --overlay')
                        writer = None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hr = hands.process(rgb)
            pr = pose.process(rgb) if pose is not None else None
            hs = hand_records(hr)
            ps = pose_record(pr) if pr is not None else None
            if hs:
                hands_hit += 1
            if ps:
                pose_hit += 1
            frames.append({
                'i': cur,
                't': int(round(cur / src_fps * 1000)),
                'hands': hs,
                'pose': ps,
            })
            if writer is not None:
                txt = '%s  f=%d  t=%.2fs  hands=%d  pose=%s' % (
                    label, cur, cur / src_fps, len(hs), 'Y' if ps else 'N')
                writer.write(draw_preview(frame.copy(), hs, ps, txt))
            kept += 1
    finally:
        cap.release()
        hands.close()
        if pose is not None:
            pose.close()
        if writer is not None:
            writer.release()

    if not frames:
        print('  [失败] %s 没有读到任何帧' % rel)
        return {'label': label, 'error': 'no frames'}

    dur_ms = int(round((fi - 1) / src_fps * 1000)) if fi > 1 else 0
    payload = {
        'format': FORMAT_NAME,
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'label': label,
        'speaker': speaker,
        'index': idx,
        'source': {
            'type': 'video',
            'name': rel,
            'width': w,
            'height': h,
            'srcFps': round(src_fps, 3),
            'stride': stride,
            'sampledFps': round(sampled_fps, 3),
            'rotate': rotate,
            'mirrored': bool(args.mirror),
            'srcFrames': fi,
        },
        'options': {
            'tool': 'tools/extract_skeleton.py',
            'hands': {'maxNumHands': args.max_hands, 'modelComplexity': args.hand_complexity,
                      'minDetectionConfidence': args.min_hand_conf,
                      'minTrackingConfidence': args.min_track_conf},
            'pose': {'enabled': pose is not None, 'modelComplexity': args.pose_complexity,
                     'minDetectionConfidence': args.min_pose_conf,
                     'minTrackingConfidence': args.min_pose_conf},
        },
        'coordSpec': COORD_SPEC,
        'stats': {
            'frameCount': len(frames),
            'handsDetected': hands_hit,
            'poseDetected': pose_hit,
            'avgHands': round(float(np.mean([len(f['hands']) for f in frames])), 3),
            'durationMs': dur_ms,
            'extractSec': round(time.time() - t_start, 1),
        },
        'frames': frames,
    }
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(',', ':'))

    hr_ratio = hands_hit / float(len(frames))
    pr_ratio = pose_hit / float(len(frames))
    flag = ''
    if hr_ratio < 0.5:
        flag = '  ← 手部检出率偏低！'
    print('  [完成] %-28s 词=%-8s %d帧/%.1fs  手%.0f%%  身%.0f%%  %.1fs%s'
          % (rel, label, len(frames), dur_ms / 1000.0, hr_ratio * 100, pr_ratio * 100,
             time.time() - t_start, flag))
    if hr_ratio < 0.5:
        print('         排查建议：--rotate 90/270（竖拍方向）、降低 --min-hand-conf 0.4、'
              '确认双手在画面内且不被裁掉、光线充足；或先用 skeleton-recorder.html 摄像头实时看骨骼。')
    if args.overlay and writer is not None:
        print('         预览：%s' % os.path.join(args.preview, out_name + '.mp4'))
    return {'label': label, 'speaker': speaker, 'frames': len(frames),
            'handRatio': round(hr_ratio, 3), 'poseRatio': round(pr_ratio, 3), 'path': out_path}


def main():
    ap = argparse.ArgumentParser(
        description='MP4 → 骨骼序列 JSON（自建手语数据集第 1 步）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default='dataset/video', help='视频目录（默认 dataset/video）')
    ap.add_argument('--out', default='dataset/skeleton', help='骨骼 JSON 输出目录')
    ap.add_argument('--preview', default='dataset/preview', help='预览视频输出目录')
    ap.add_argument('--fps', type=float, default=30.0, help='采样帧率，0=逐帧不降采样（默认 30）')
    ap.add_argument('--max-frames', type=int, default=0, help='单个视频最多保留帧数，0=不限')
    ap.add_argument('--max-hands', type=int, default=2)
    ap.add_argument('--hand-complexity', type=int, default=1, choices=[0, 1])
    ap.add_argument('--pose-complexity', type=int, default=1, choices=[0, 1, 2])
    ap.add_argument('--min-hand-conf', type=float, default=0.5)
    ap.add_argument('--min-track-conf', type=float, default=0.5)
    ap.add_argument('--min-pose-conf', type=float, default=0.5)
    ap.add_argument('--no-pose', action='store_true', help='只要手部骨骼（更快）')
    ap.add_argument('--rotate', default='0', help='顺时针旋转角度：0/90/180/270 或 auto（读视频元数据）')
    ap.add_argument('--mirror', action='store_true', help='推理前水平翻转（自拍镜像视频时用）')
    ap.add_argument('--overlay', action='store_true', help='额外输出骨骼叠加预览 mp4')
    ap.add_argument('--only', default='', help='只处理指定词条')
    ap.add_argument('--limit', type=int, default=0, help='最多处理多少个视频（调试用）')
    ap.add_argument('--force', action='store_true', help='忽略已有结果，强制重跑')
    ap.add_argument('--list', action='store_true', help='只列出命名解析结果，不做提取')
    args = ap.parse_args()

    for d in (args.src, args.out, args.preview):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)

    videos = find_videos(args.src)
    if not videos:
        print('在 %s 下没有找到视频。请把 MP4 放进去，命名规则：' % args.src)
        print('  A. 平铺：  dataset/video/今天_s01_003.mp4   （词_说话人_序号）')
        print('  B. 分文件夹：dataset/video/今天/s01_003.mp4 （文件夹名=词）')
        return 1

    print('找到 %d 个视频，命名解析结果：' % len(videos))
    for v in videos:
        label, speaker, idx = parse_label(v, args.src)
        print('  %-42s → 词=%-10s 说话人=%-5s 序号=%s'
              % (os.path.relpath(v, args.src).replace(os.sep, '/'), label, speaker, idx))
    if args.list:
        words = sorted({parse_label(v, args.src)[0] for v in videos})
        print('\n共 %d 个词条：%s' % (len(words), ' '.join(words)))
        return 0

    todo = videos[:args.limit] if args.limit > 0 else videos
    print('\n开始提取（采样 %.0f fps，手部 complexity=%d，身体=%s）：'
          % (args.fps, args.hand_complexity, 'off' if args.no_pose else 'complexity=%d' % args.pose_complexity))
    results = []
    for v in todo:
        r = process_video(v, args, args.src, args.out, args.preview)
        if r:
            results.append(r)

    done = [r for r in results if not r.get('skipped') and not r.get('error')]
    print('\n汇总：%d 个视频处理完成，%d 个跳过，%d 个失败'
          % (len(done), len([r for r in results if r.get('skipped')]),
             len([r for r in results if r.get('error')])))
    if done:
        words = {}
        for r in done:
            words.setdefault(r['label'], 0)
            words[r['label']] += 1
        print('词条覆盖：%s' % '、'.join('%s×%d' % (k, words[k]) for k in sorted(words)))
        low = [r['label'] for r in done if r.get('handRatio', 1) < 0.5]
        if low:
            print('⚠ 手部检出率偏低（建议先看 dataset/preview 预览）：%s' % '、'.join(sorted(set(low))))
        print('\n下一步：python3 tools/build_dataset.py   # 骨骼 JSON → 归一化数据集 + 自建-templates.js')
    return 0


if __name__ == '__main__':
    sys.exit(main())
