#!/bin/bash
# 双击本文件即可：自动启动本地服务（若未启动）并打开骨骼录制页。
# 为什么不能直接双击 skeleton-recorder.html：
#   file:// 协议下浏览器禁止访问摄像头，必须通过 http://localhost 打开。
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8080/skeleton-recorder.html"

if curl -s -o /dev/null --max-time 2 "$URL"; then
  echo "本地服务已在运行，直接打开页面。"
else
  echo "启动本地服务 serve.py（端口 8080）..."
  python3 serve.py &
  SRV=$!
  for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    curl -s -o /dev/null --max-time 1 "$URL" && break
  done
fi

# MediaPipe 依赖自检：本地 vendor/ 不全时页面会回退 CDN（国内可能很慢，甚至一根骨骼都不出）
if ! bash tools/fetch_vendor.sh --check >/dev/null 2>&1; then
  echo "⚠ MediaPipe 本地依赖不全，本次会走 CDN（慢/可能失败）。建议执行一次："
  echo "     bash tools/fetch_vendor.sh        # 约 74MB，下载后离线可用"
fi

open "$URL"
echo
echo "已打开：$URL"
echo "------------------------------------------------------------"
echo "录制前自检（30 秒）："
echo "  1. 顶部「依赖」应显示 已就绪；「FPS」≥ 20 为正常"
echo "     若显示 加载失败 → 先跑 bash tools/fetch_vendor.sh 再刷新本页"
echo "  2. 按 T 打开对齐自检：绿框必须恰好压在视频画面四条边上"
echo "  3. 按 I 显示 21 点编号：0=手腕，4/8/12/16/20=五指指尖，应贴在指关节上"
echo "  4. 按 Z 冻结帧，可逐点静态检查骨骼是否贴合"
echo "录屏时："
echo "  按 H 关掉 HUD、按 G 关掉参考线、按 F 全屏舞台，画面里只剩视频+骨骼"
echo "------------------------------------------------------------"
echo "本窗口请保持打开（服务在运行）。结束后回到本窗口按 Ctrl+C 停止。"
[ -n "$SRV" ] && wait $SRV
exit 0
