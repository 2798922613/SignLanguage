#!/usr/bin/env bash
# =============================================================================
# 下载 MediaPipe 离线依赖到 vendor/mediapipe/npm/@mediapipe/<包>@<版本>/
#
# 为什么需要：index.html 与 skeleton-recorder.html 原先在运行时从 cdn.jsdelivr.net
# 拉 hands.js / pose.js 及其 wasm + tflite 模型。国内网络下 jsdelivr 经常挂起十几秒
# 甚至完全不通，结果就是"页面开了、摄像头也亮了，但一根骨骼都不画"，识别页还会一直
# 提示"静态判据：没拿到手+肩 → 请确认身体骨骼已开启"。
# 下到本地后走 本地 vendor → jsdelivr → unpkg 的顺序（见 mp-loader.js），秒开。
#
# 用法（在仓库任意位置执行）：
#   bash tools/fetch_vendor.sh
#   NPM_REGISTRY=https://registry.npmmirror.com bash tools/fetch_vendor.sh   # 国内镜像更快
#
# 版本必须与 mp-loader.js / gesture-recognizer.js / skeleton-recorder.html 里钉的完全一致，
# 否则录制页与识别页的 landmark 会有系统性偏差，模板整体对不上。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/vendor/mediapipe/npm/@mediapipe"
REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"

HANDS_VER=0.4.1675469240
POSE_VER=0.5.1675469404
DRAW_VER=0.3.1675466124
CAM_VER=0.3.1675466862
PKGS="hands:$HANDS_VER pose:$POSE_VER drawing_utils:$DRAW_VER camera_utils:$CAM_VER"

# 每个包运行时真正会取的文件（与 mp-loader.js 的 assets 列表保持一致）
REQUIRED="
hands@$HANDS_VER/hands.js
hands@$HANDS_VER/hands.binarypb
hands@$HANDS_VER/hands_solution_packed_assets.data
hands@$HANDS_VER/hands_solution_packed_assets_loader.js
hands@$HANDS_VER/hands_solution_simd_wasm_bin.js
hands@$HANDS_VER/hands_solution_simd_wasm_bin.wasm
hands@$HANDS_VER/hands_solution_wasm_bin.js
hands@$HANDS_VER/hands_solution_wasm_bin.wasm
hands@$HANDS_VER/hand_landmark_full.tflite
hands@$HANDS_VER/hand_landmark_lite.tflite
pose@$POSE_VER/pose.js
pose@$POSE_VER/pose_web.binarypb
pose@$POSE_VER/pose_solution_packed_assets.data
pose@$POSE_VER/pose_solution_packed_assets_loader.js
pose@$POSE_VER/pose_solution_simd_wasm_bin.js
pose@$POSE_VER/pose_solution_simd_wasm_bin.wasm
pose@$POSE_VER/pose_solution_wasm_bin.js
pose@$POSE_VER/pose_solution_wasm_bin.wasm
pose@$POSE_VER/pose_landmark_full.tflite
pose@$POSE_VER/pose_landmark_lite.tflite
drawing_utils@$DRAW_VER/drawing_utils.js
camera_utils@$CAM_VER/camera_utils.js
"

check_only=0
[ "${1:-}" = "--check" ] && check_only=1

verify() {
  local missing=0 f
  for f in $REQUIRED; do
    if [ ! -s "$DEST/$f" ]; then echo "  缺失: $f"; missing=1; fi
  done
  return $missing
}

if [ "$check_only" = "1" ]; then
  if verify; then echo "vendor 依赖完整 ✅  $DEST"; else echo "vendor 依赖不完整 ❌ 执行: bash tools/fetch_vendor.sh"; exit 1; fi
  exit 0
fi

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

for spec in $PKGS; do
  name="${spec%%:*}"; ver="${spec##*:}"
  echo "==> 下载 @mediapipe/$name@$ver"
  curl -fSL --retry 2 --connect-timeout 15 -m 900 -o "$TMP/$name.tgz" \
    "$REGISTRY/@mediapipe/$name/-/$name-$ver.tgz"
  echo "    解包到 vendor/mediapipe/npm/@mediapipe/$name@$ver/"
  rm -rf "$DEST/$name@$ver"
  mkdir -p "$DEST/$name@$ver"
  tar -xzf "$TMP/$name.tgz" -C "$DEST/$name@$ver" --strip-components=1
done

echo "==> 校验关键资产"
if verify; then
  echo "全部齐全 ✅"
  du -sh "$ROOT/vendor"
  echo "下一步：bash 打开手势交互页.command（或 python3 serve.py）后刷新浏览器即可，无需联网。"
else
  echo "仍有文件缺失 ❌ 请重跑本脚本，或改用镜像：NPM_REGISTRY=https://registry.npmmirror.com bash tools/fetch_vendor.sh"
  exit 1
fi
