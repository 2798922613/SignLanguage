/**
 * MediaPipe Hands 手势识别器 V7
 * 17个硬编码基础词汇 + SLR500(500词)骨骼模板匹配
 *
 * 识别优先级：
 * 1. 硬编码规则（17个基础词汇）- 使用手部详细特征（手指伸展、方向等）
 * 2. SLR500模板匹配（500个词汇）- 使用手臂骨骼轨迹匹配
 *
 * V7 稳定性修复：
 * - 身体骨骼与手部骨骼统一在一次 clearRect 之后绘制（此前手部绘制会擦掉身体骨骼）
 * - 识别与绘制彻底解耦，任一方抛错都不影响另一方
 * - 帧循环内所有 send 都包 try/catch，Pose 失败会自动降级，绝不停摆
 * - Pose 改为隔帧推理，降低 GPU/内存压力
 */

// MediaPipe Pose 33点连接关系（模块级常量，避免每帧重建数组）
const POSE_CONNECTIONS = [
  [0, 1], [1, 2], [2, 3], [3, 7],
  [0, 4], [4, 5], [5, 6], [6, 8],
  [9, 10],
  [11, 12], [11, 23], [12, 24], [23, 24],
  [11, 13], [13, 15],
  [12, 14], [14, 16],
  [15, 17], [15, 19], [17, 19], [19, 21],
  [16, 18], [16, 20], [18, 20], [20, 22],
  [23, 25], [25, 27], [27, 29], [27, 31], [29, 31],
  [24, 26], [26, 28], [28, 30], [28, 32], [30, 32]
];

class GestureRecognizer {
  constructor() {
    this.hands = null;
    this.pose = null;
    this.onGestureResult = null;
    this.onError = null;

    // 硬编码防抖与锁定
    this.gestureCount = 0;
    this.lastGestureName = "";
    this.lastGestureTime = 0;
    this.DEBOUNCE_THRESHOLD = 8;
    this.GESTURE_LOCK_TIME = 2000;

    // 词序列队列
    this.gestureQueue = [];
    this.QUEUE_MAX_LENGTH = 6;

    // === SLR500 模板匹配相关 ===
    this.latestPoseResults = null;
    this.frameBuffer = [];
    this.BUFFER_SIZE = 40;          // 缓冲40帧（约1.3秒@30fps）
    this.MIN_BUFFER_FOR_MATCH = 15; // 最少15帧才尝试匹配
    this.MATCH_INTERVAL = 8;        // 每8帧尝试一次匹配
    this.slr500FrameCount = 0;
    this.lastSLR500MatchTime = 0;
    this.SLR500_COOLDOWN = 3000;    // SLR500匹配冷却3秒
    this.SLR500_THRESHOLD = 1.8;    // 距离阈值（越小越严格）
    this.MOVEMENT_THRESHOLD = 0.003; // 运动检测阈值
    this.HAND_VISIBLE_FRAMES = 0;   // 连续检测到手的帧数

    // 渲染 / 帧循环状态
    this.frameIndex = 0;
    this.poseEnabled = true;
    this.poseFailCount = 0;
    this.reportedErrors = {};

    // 17个基础词汇列表（硬编码优先）
    this.BASIC_WORDS = ['我','你','好','不','很','喜欢','回','天气','吃','饭','今天','爱','家','苹果','认识','高兴','了'];
  }

  async init(videoElement, canvasElement) {
    // CDN 依赖检查：缺失时给出明确原因，而不是静默黑屏
    if (typeof Hands !== 'function') {
      throw new Error('MediaPipe Hands 未加载（CDN 脚本被拦截或网络不可用）');
    }
    if (typeof Camera !== 'function') {
      throw new Error('MediaPipe Camera Utils 未加载（CDN 脚本被拦截或网络不可用）');
    }

    canvasElement.width = 640;
    canvasElement.height = 480;

    // 初始化 Hands（必需）
    this.hands = new Hands({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/hands/${file}`
    });
    this.hands.setOptions({
      maxNumHands: 2,
      modelComplexity: 1,
      minDetectionConfidence: 0.7,
      minTrackingConfidence: 0.7
    });

    // 初始化 Pose（可选：失败不影响手部识别）
    if (typeof Pose === 'function') {
      try {
        this.pose = new Pose({
          locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${file}`
        });
        this.pose.setOptions({
          modelComplexity: 1,
          smoothLandmarks: true,
          enableSegmentation: false,
          minDetectionConfidence: 0.5,
          minTrackingConfidence: 0.5
        });
        // Pose 回调只保存结果，绘制统一由 renderSkeletons 负责
        this.pose.onResults((results) => {
          this.latestPoseResults = results;
        });
      } catch (err) {
        this.pose = null;
        this.poseEnabled = false;
        this.reportError('Pose 初始化', err);
      }
    } else {
      this.poseEnabled = false;
      this.reportError('Pose', 'pose.js 未加载，仅使用手部识别');
    }

    // Hands 回调：先识别、后绘制，各自 try/catch 互不牵连
    this.hands.onResults((results) => {
      try {
        this.analyzeGestures(results, canvasElement);
      } catch (err) {
        this.reportError('识别流程', err);
      }
      this.renderSkeletons(results, canvasElement);
    });

    // 启动摄像头：帧循环里的异常必须全部吞掉，否则 Camera 会停止推帧
    const camera = new Camera(videoElement, {
      onFrame: async () => {
        try {
          await this.hands.send({ image: videoElement });
        } catch (err) {
          this.reportError('Hands 推理', err);
        }

        if (this.poseEnabled && this.pose && (this.frameIndex++ % 2 === 0)) {
          try {
            await this.pose.send({ image: videoElement });
          } catch (err) {
            this.poseFailCount++;
            this.reportError('Pose 推理', err);
            if (this.poseFailCount >= 3) {
              this.poseEnabled = false;
              this.reportError('Pose', '连续失败已自动停用身体骨骼，手部识别不受影响');
            }
          }
        }
      },
      width: 640,
      height: 480
    });

    try {
      await camera.start();
    } catch (err) {
      const msg = (err && err.message) ? err.message : String(err);
      throw new Error('摄像头启动失败：' + msg + '（请确认已允许摄像头权限，并通过 localhost 或 https 访问）');
    }
  }

  /**
   * 统一渲染：一帧只 clear 一次，先画身体骨骼再画手部骨骼。
   * 之前的实现里 drawHands 会 clearRect 掉 drawPose 刚画好的内容，导致身体骨骼看不见。
   */
  renderSkeletons(handResults, canvasElement) {
    try {
      if (typeof drawConnectors !== 'function' || typeof drawLandmarks !== 'function') {
        this.reportError('骨骼绘制', 'drawing_utils 未加载，drawConnectors/drawLandmarks 不可用');
        return;
      }

      const canvasCtx = canvasElement.getContext('2d');
      canvasCtx.save();
      canvasCtx.clearRect(0, 0, canvasElement.width, canvasElement.height);

      const poseLandmarks = this.latestPoseResults && this.latestPoseResults.poseLandmarks;
      if (poseLandmarks) {
        drawConnectors(canvasCtx, poseLandmarks, POSE_CONNECTIONS, { color: '#00BFFF', lineWidth: 1.5 });
        drawLandmarks(canvasCtx, poseLandmarks, { color: '#FF69B4', lineWidth: 1, radius: 2 });
      }

      if (handResults && handResults.multiHandLandmarks && typeof HAND_CONNECTIONS !== 'undefined') {
        for (const landmarks of handResults.multiHandLandmarks) {
          drawConnectors(canvasCtx, landmarks, HAND_CONNECTIONS, { color: '#FFDE59', lineWidth: 4 });
          drawLandmarks(canvasCtx, landmarks, { color: '#FF3131', lineWidth: 2, radius: 4 });
        }
      }

      canvasCtx.restore();
    } catch (err) {
      this.reportError('骨骼绘制', err);
    }
  }

  /**
   * 错误上报：同类错误只报一次避免刷屏，同时抛给上层 UI
   */
  reportError(stage, err) {
    const message = (err && err.message) ? err.message : String(err);
    const key = stage + '|' + message;
    if (this.reportedErrors[key]) return;
    this.reportedErrors[key] = true;
    console.error('[GestureRecognizer] ' + stage + ':', err);
    if (this.onError) {
      try { this.onError(stage, message); } catch (ignored) {}
    }
  }

  // ============================================================
  // 硬编码规则：17个基础词汇
  // ============================================================
  matchHardcodedRules(results) {
    const hands = results.multiHandLandmarks;
    if (!hands || hands.length === 0) return "";

    const h1 = hands[0];
    const isTwoHands = hands.length >= 2;
    const h2 = isTwoHands ? hands[1] : null;
    const getDist = (a, b) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z);
    const isExtended = (tip, pip, mcp) => getDist(tip, mcp) > getDist(pip, mcp);

    const thumbUp = h1[4].y < h1[3].y && h1[4].y < h1[2].y;
    const indexUp = isExtended(h1[8], h1[7], h1[5]);
    const middleUp = isExtended(h1[12], h1[11], h1[9]);
    const ringUp = isExtended(h1[16], h1[15], h1[13]);
    const pinkyUp = isExtended(h1[20], h1[19], h1[17]);

    if (isTwoHands) {
      const h2ThumbDown = h2[4].y > h2[3].y;
      const h1ThumbDown = h1[4].y > h1[3].y;
      if (getDist(h1[4], h2[4]) < 0.12 && getDist(h1[8], h2[8]) < 0.12 && h1ThumbDown && h2ThumbDown) return "爱";
      if (getDist(h1[8], h2[8]) < 0.1 && h1[8].y < h1[5].y) return "家";
      if (indexUp && !middleUp && getDist(h1[8], h2[8]) < 0.2) return "苹果";
      if (indexUp && middleUp && !ringUp) return "认识";
      if (h1[0].y < h1[9].y && h2[0].y < h2[9].y && h1[9].y > 0.4 && h2[9].y > 0.4) return "高兴";
    } else {
      const dotL = (h1[4].x - h1[2].x) * (h1[8].x - h1[5].x) + (h1[4].y - h1[2].y) * (h1[8].y - h1[5].y);
      if (thumbUp && indexUp && !middleUp && !ringUp && !pinkyUp && Math.abs(dotL) < 0.02) return "很";
      if (thumbUp && indexUp && !middleUp && !ringUp && !pinkyUp && h1[8].y > 0.55) return "喜欢";
      if (h1[4].y > h1[0].y && h1[8].y > h1[0].y && h1[12].y > h1[0].y && h1[16].y > h1[0].y && h1[20].y > h1[0].y) return "了";
      if (thumbUp && pinkyUp && !indexUp && !middleUp && !ringUp) return "回";
      if (indexUp && !middleUp && h1[8].y > h1[5].y) return "我";
      if (indexUp && !middleUp && h1[8].z < -0.1) return "你";
      if (thumbUp && !indexUp) return "好";
      if (pinkyUp && !indexUp) return "不";
      if (indexUp && !middleUp && h1[8].y < h1[5].y) return "天气";
      if (indexUp && middleUp && !ringUp) return "吃";
      if (getDist(h1[4], h1[8]) < 0.05) return "饭";
      if (h1[9].y > 0.6) return "今天";
    }
    return "";
  }

  // ============================================================
  // SLR500 模板匹配
  // ============================================================

  /**
   * 从MediaPipe Pose提取归一化骨骼特征
   * 特征顺序: ElbowL(3), WristL(3), HandL(3), ElbowR(3), WristR(3), HandR(3) = 18维
   * 归一化: 以肩膀中点为中心, 肩宽为缩放因子
   */
  extractPoseFeatures(poseLandmarks, handLandmarks) {
    if (!poseLandmarks) return null;

    const leftShoulder = poseLandmarks[11];
    const rightShoulder = poseLandmarks[12];
    const leftElbow = poseLandmarks[13];
    const rightElbow = poseLandmarks[14];
    const leftWrist = poseLandmarks[15];
    const rightWrist = poseLandmarks[16];

    // 至少需要肩膀和一侧手臂
    if (!leftShoulder || !rightShoulder) return null;
    if ((!leftElbow || !leftWrist) && (!rightElbow || !rightWrist)) return null;

    // 身体中心 = 肩膀中点
    const cx = (leftShoulder.x + rightShoulder.x) / 2;
    const cy = (leftShoulder.y + rightShoulder.y) / 2;
    const cz = (leftShoulder.z + rightShoulder.z) / 2;

    // 肩宽
    const sw = Math.hypot(
      leftShoulder.x - rightShoulder.x,
      leftShoulder.y - rightShoulder.y,
      (leftShoulder.z - rightShoulder.z)
    );
    if (sw < 0.01) return null;

    // 归一化：减中心、除肩宽，并翻转 x/y 轴以对齐 Kinect 坐标系
    // Kinect: x正=受试者右侧, y正=向上；MediaPipe: x正=图像右(受试者左侧), y正=向下
    const normalize = (landmark) => {
      if (!landmark) return [0, 0, 0];
      return [
        -(landmark.x - cx) / sw,
        -(landmark.y - cy) / sw,
        (landmark.z - cz) / sw
      ];
    };

    // 获取手部位置（优先使用MediaPipe Hands的详细数据）
    let handL = leftWrist;
    let handR = rightWrist;

    if (handLandmarks && handLandmarks.length > 0) {
      // 使用手掌中心（landmark 9 = 中指MCP关节）作为Hand位置
      for (const hand of handLandmarks) {
        const wrist = hand[0];
        const midMCP = hand[9];
        // 判断是左手还是右手（根据x坐标相对肩膀的位置）
        const handCx = wrist.x;
        if (handCx < cx) {
          // 图像左侧 = 受试者右手 → HandR
          handR = midMCP || wrist;
        } else {
          // 图像右侧 = 受试者左手 → HandL
          handL = midMCP || wrist;
        }
      }
    }

    const features = [
      ...normalize(leftElbow || leftShoulder),
      ...normalize(leftWrist || leftShoulder),
      ...normalize(handL || leftWrist || leftShoulder),
      ...normalize(rightElbow || rightShoulder),
      ...normalize(rightWrist || rightShoulder),
      ...normalize(handR || rightWrist || rightShoulder)
    ];

    return features;
  }

  /**
   * 缓冲帧并尝试SLR500匹配
   * 返回匹配结果 {word, distance, confidence} 或 null
   */
  bufferAndMatchSLR500(handsResults) {
    const poseLandmarks = this.latestPoseResults ? this.latestPoseResults.poseLandmarks : null;
    const handLandmarks = handsResults ? handsResults.multiHandLandmarks : null;

    // 提取特征并缓冲
    const features = this.extractPoseFeatures(poseLandmarks, handLandmarks);

    if (features) {
      this.frameBuffer.push(features);
      if (this.frameBuffer.length > this.BUFFER_SIZE) {
        this.frameBuffer.shift();
      }
      this.HAND_VISIBLE_FRAMES++;
    } else {
      this.HAND_VISIBLE_FRAMES = Math.max(0, this.HAND_VISIBLE_FRAMES - 1);
    }

    this.slr500FrameCount++;

    // 检查是否满足匹配条件
    if (this.slr500FrameCount % this.MATCH_INTERVAL !== 0) return null;
    if (this.frameBuffer.length < this.MIN_BUFFER_FOR_MATCH) return null;
    if (Date.now() - this.lastSLR500MatchTime < this.SLR500_COOLDOWN) return null;

    // 检查是否有足够的运动（排除静止状态）
    if (!this.hasSignificantMovement()) return null;

    // 需要检测到手
    if (this.HAND_VISIBLE_FRAMES < 8) return null;

    return this.matchSLR500();
  }

  /**
   * 检测缓冲区中是否有显著运动
   */
  hasSignificantMovement() {
    const buf = this.frameBuffer;
    if (buf.length < 5) return false;

    // 计算所有帧的特征方差
    let totalVariance = 0;
    const numFeatures = buf[0].length;

    for (let f = 0; f < numFeatures; f++) {
      let mean = 0;
      for (let i = 0; i < buf.length; i++) mean += buf[i][f];
      mean /= buf.length;

      let variance = 0;
      for (let i = 0; i < buf.length; i++) {
        const diff = buf[i][f] - mean;
        variance += diff * diff;
      }
      variance /= buf.length;
      totalVariance += variance;
    }

    return (totalVariance / numFeatures) > this.MOVEMENT_THRESHOLD;
  }

  /**
   * SLR500模板匹配核心算法
   * 将缓冲区帧下采样为10个关键帧，与500个词汇模板进行距离比较
   */
  matchSLR500() {
    if (typeof SLR500_TEMPLATES === 'undefined' || typeof SLR500_WORDS === 'undefined') {
      return null;
    }

    const buffer = this.frameBuffer;
    const n = buffer.length;
    const numKeyframes = 10;

    // 下采样到10个关键帧
    const keyframes = [];
    for (let i = 0; i < numKeyframes; i++) {
      const srcIdx = numKeyframes > 1 ? i * (n - 1) / (numKeyframes - 1) : 0;
      const idxLow = Math.floor(srcIdx);
      const idxHigh = Math.min(idxLow + 1, n - 1);
      const frac = srcIdx - idxLow;

      const low = buffer[idxLow];
      const high = buffer[idxHigh];
      const interpolated = [];
      for (let j = 0; j < low.length; j++) {
        interpolated.push(low[j] * (1 - frac) + high[j] * frac);
      }
      keyframes.push(interpolated);
    }

    // 展平为查询向量
    const query = keyframes.flat();

    // 遍历所有模板，计算RMS距离
    let bestWord = null;
    let bestDist = Infinity;
    let secondBestDist = Infinity;

    for (let w = 0; w < SLR500_WORDS.length; w++) {
      const word = SLR500_WORDS[w];
      const template = SLR500_TEMPLATES[word];
      if (!template) continue;

      // 跳过17个基础词汇（由硬编码规则处理）
      if (this.BASIC_WORDS.includes(word)) continue;

      let sumSqDiff = 0;
      for (let i = 0; i < query.length; i++) {
        const diff = query[i] - template[i];
        sumSqDiff += diff * diff;
      }
      const dist = Math.sqrt(sumSqDiff / query.length);

      if (dist < bestDist) {
        secondBestDist = bestDist;
        bestDist = dist;
        bestWord = word;
      } else if (dist < secondBestDist) {
        secondBestDist = dist;
      }
    }

    // 记录距离用于调试
    if (bestWord) {
      console.log(`[SLR500] 候选: ${bestWord} 距离=${bestDist.toFixed(4)} 次佳=${secondBestDist.toFixed(4)} 比值=${(bestDist/secondBestDist).toFixed(3)}`);
    }

    // 阈值检查
    if (bestDist < this.SLR500_THRESHOLD && bestWord) {
      // 额外检查：最佳匹配需要比次佳匹配明显更好
      const ratio = bestDist / secondBestDist;
      if (ratio < 0.85) {
        this.lastSLR500MatchTime = Date.now();
        this.frameBuffer = [];
        this.HAND_VISIBLE_FRAMES = 0;
        console.log(`[SLR500] ✓ 确认匹配: ${bestWord} (距离=${bestDist.toFixed(4)})`);
        return {
          word: bestWord,
          distance: bestDist,
          confidence: Math.max(0, 1 - bestDist / this.SLR500_THRESHOLD)
        };
      }
    }

    return null;
  }

  // ============================================================
  // 主识别流程
  // ============================================================
  analyzeGestures(results, canvasElement) {
    // 冷却期检查
    if (Date.now() - this.lastGestureTime < this.GESTURE_LOCK_TIME) return;

    let currentFrameGesture = "";

    // Step 1: 硬编码规则（17个基础词汇）- 优先级最高
    const hardcodedResult = this.matchHardcodedRules(results);

    // Step 2: SLR500模板匹配
    const slr500Result = this.bufferAndMatchSLR500(results);

    // Step 3: 决策
    if (hardcodedResult) {
      currentFrameGesture = hardcodedResult;
      // 硬编码匹配成功时清空SLR500缓冲区
      this.frameBuffer = [];
      this.HAND_VISIBLE_FRAMES = 0;
    } else if (slr500Result) {
      currentFrameGesture = slr500Result.word;
    }

    // Step 4: 防抖（硬编码词汇使用帧级防抖）
    if (hardcodedResult) {
      if (currentFrameGesture === this.lastGestureName) {
        this.gestureCount++;
      } else {
        this.gestureCount = 0;
        this.lastGestureName = currentFrameGesture;
      }

      if (this.gestureCount >= this.DEBOUNCE_THRESHOLD) {
        this.triggerGesture(currentFrameGesture);
        this.gestureCount = 0;
      }
    } else if (slr500Result) {
      // SLR500匹配已经通过冷却期和阈值检查，直接触发
      this.triggerGesture(currentFrameGesture);
    }
  }

  triggerGesture(name) {
    if (!name || name === "") return;
    if (this.gestureQueue[this.gestureQueue.length - 1] === name) return;
    this.gestureQueue.push(name);
    if (this.gestureQueue.length > this.QUEUE_MAX_LENGTH) this.gestureQueue.shift();

    this.lastGestureTime = Date.now();

    if (this.onGestureResult) {
      this.onGestureResult(name, this.gestureQueue);
    }
  }
}
