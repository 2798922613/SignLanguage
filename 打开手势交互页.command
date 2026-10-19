#!/bin/bash
# 双击本文件即可：自动启动本地服务（若未启动）并打开手势交互页。
# 为什么不能直接双击 index.html：
#   file:// 协议下浏览器禁止访问摄像头，必须通过 http://localhost 打开。
cd "$(dirname "$0")" || exit 1
URL="http://localhost:8080/index.html"

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
echo "当前识别词库：自建数据集（10 词，全部走静态判据 STATIC_RULES / STATIC_RULES2）"
echo "  单手 001-008/010：我 你 今天 天气 好 吃 米饭 了 很"
echo "  双手 009：认识（两只手同时检出时先跑双手判据）"
echo "做完动作停住约 0.5 秒即出图；识别页右下角 HUD 会实时显示命中/最接近哪个词、差哪一维。"
echo "没有骨骼时：HUD（按 D 显示）会写清原因；身体骨骼被自动停用后按 P 重试，不用刷新。"
echo "SLR500 已不再加载。"
echo "------------------------------------------------------------"
echo "本窗口请保持打开（服务在运行）。结束后回到本窗口按 Ctrl+C 停止。"
[ -n "$SRV" ] && wait $SRV
exit 0
