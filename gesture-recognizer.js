/**
 * MediaPipe Hands 手势识别器 V5
 * 使用【组词效果/index.html】的识别代码 + Pose身体骨骼检测
 * 17个词汇：我、你、好、不、很、喜欢、回、天气、吃、饭、今天、爱、家、苹果、认识、高兴、了
 * 
 * slr500_words_joints 包含手部骨骼+身体骨骼数据，
 * 17个词汇检测身体骨骼作为辅助上下文，手部骨骼描述准确即可识别词汇。
 */

class GestureRecognizer {
  constructor() {
    this.hands = null;
    this.pose = null;
    this.onGestureResult = null;
    
    // 防抖与锁定（与组词效果/index.html一致）
    this.gestureCount = 0;
    this.lastGestureName = "";
    this.lastGestureTime = 0;
    this.DEBOUNCE_THRESHOLD = 8;   // 连续8帧相同手势才确认
    this.GESTURE_LOCK_TIME = 2000; // 2秒锁定
    
    // 词序列队列（与组词效果/index.html一致）
    this.gestureQueue = [];
    this.QUEUE_MAX_LENGTH = 6;
  }

  async init(videoElement, canvasElement) {
    const canvasCtx = canvasElement.getContext('2d');
    
    // 初始化 Pose（身体骨骼）- slr500_words_joints包含身体骨骼数据
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
    
    // 初始化 Hands（手部骨骼）- 与组词效果/index.html一致
    this.hands = new Hands({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/hands/${file}`
    });
    
    this.hands.setOptions({
      maxNumHands: 2,
      modelComplexity: 1,
      minDetectionConfidence: 0.7,
      minTrackingConfidence: 0.7
    });
    
    // Pose 绘制身体骨骼
    this.pose.onResults((results) => {
      this.drawPose(results, canvasElement);
    });
    
    // Hands 识别结果处理 - 使用组词效果/index.html的识别代码
    this.hands.onResults((results) => {
      this.drawHands(results, canvasElement);
      this.analyzeGestures(results, canvasElement);
    });
    
    // 启动摄像头
    const camera = new Camera(videoElement, {
      onFrame: async () => {
        await this.hands.send({ image: videoElement });
        await this.pose.send({ image: videoElement });
      },
      width: 640,
      height: 480
    });
    
    canvasElement.width = 640;
    canvasElement.height = 480;
    camera.start();
  }

  /**
   * 绘制手部骨骼 - 与组词效果/index.html一致
   */
  drawHands(results, canvasElement) {
    const canvasCtx = canvasElement.getContext('2d');
    canvasCtx.save();
    canvasCtx.clearRect(0, 0, canvasElement.width, canvasElement.height);
    if (results.multiHandLandmarks) {
      for (const landmarks of results.multiHandLandmarks) {
        drawConnectors(canvasCtx, landmarks, HAND_CONNECTIONS, {color: '#FFDE59', lineWidth: 4});
        drawLandmarks(canvasCtx, landmarks, {color: '#FF3131', lineWidth: 2, radius: 4});
      }
    }
    canvasCtx.restore();
  }

  /**
   * 绘制身体骨骼（33个关节点）
   * slr500_words_joints包含身体骨骼数据，此处绘制用于可视化
   */
  drawPose(results, canvasElement) {
    const canvasCtx = canvasElement.getContext('2d');
    canvasCtx.save();
    
    if (results.poseLandmarks) {
      const poseConnections = [
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
      
      drawConnectors(canvasCtx, results.poseLandmarks, poseConnections, {
        color: '#00BFFF',
        lineWidth: 1.5
      });
      drawLandmarks(canvasCtx, results.poseLandmarks, {
        color: '#FF69B4',
        lineWidth: 1,
        radius: 2
      });
    }
    
    canvasCtx.restore();
  }

  /**
   * 手势识别核心逻辑 - 完全使用【组词效果/index.html】的代码
   * 17个词汇：16个来自组词效果/index.html + "了"使用描述检测
   * 
   * "了"的描述：一手五指张开，掌心向下，五指指尖朝下，手背面向前方，甩动一下
   * 对应代码：五指全部张开（thumbUp && indexUp && middleUp && ringUp && pinkyUp），
   *           掌心向下（手腕y < 食指指尖y && 手腕y < 小指指尖y）
   */
  analyzeGestures(results, canvasElement) {
    if (Date.now() - this.lastGestureTime < this.GESTURE_LOCK_TIME) return;

    let currentFrameGesture = "";
    const hands = results.multiHandLandmarks;

    if (hands && hands.length > 0) {
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
        // === 双手手势（5个）===
        const h2ThumbDown = h2[4].y > h2[3].y;
        const h1ThumbDown = h1[4].y > h1[3].y;
        if (getDist(h1[4], h2[4]) < 0.12 && getDist(h1[8], h2[8]) < 0.12 && h1ThumbDown && h2ThumbDown) currentFrameGesture = "爱";
        else if (getDist(h1[8], h2[8]) < 0.1 && h1[8].y < h1[5].y) currentFrameGesture = "家";
        else if (indexUp && !middleUp && getDist(h1[8], h2[8]) < 0.2) currentFrameGesture = "苹果";
        else if (indexUp && middleUp && !ringUp) currentFrameGesture = "认识";
        else if (h1[0].y < h1[9].y && h2[0].y < h2[9].y && h1[9].y > 0.4 && h2[9].y > 0.4) currentFrameGesture = "高兴";
      } else {
        // === 单手手势（12个）===
        const dotL = (h1[4].x - h1[2].x) * (h1[8].x - h1[5].x) + (h1[4].y - h1[2].y) * (h1[8].y - h1[5].y);
        if (thumbUp && indexUp && !middleUp && !ringUp && !pinkyUp && Math.abs(dotL) < 0.02) currentFrameGesture = "很";
        else if (thumbUp && indexUp && !middleUp && !ringUp && !pinkyUp && h1[8].y > 0.55) currentFrameGesture = "喜欢";
        // "了"：一手五指张开，掌心向下，五指指尖朝下，手背面向前方，甩动一下
        // 检测：5根手指指尖朝下（y值大于手腕y值），掌心向下
        else if (h1[4].y > h1[0].y && h1[8].y > h1[0].y && h1[12].y > h1[0].y && h1[16].y > h1[0].y && h1[20].y > h1[0].y) currentFrameGesture = "了";
        else if (thumbUp && pinkyUp && !indexUp && !middleUp && !ringUp) currentFrameGesture = "回";
        else if (indexUp && !middleUp && h1[8].y > h1[5].y) currentFrameGesture = "我";
        else if (indexUp && !middleUp && h1[8].z < -0.1) currentFrameGesture = "你";
        else if (thumbUp && !indexUp) currentFrameGesture = "好";
        else if (pinkyUp && !indexUp) currentFrameGesture = "不";
        else if (indexUp && !middleUp && h1[8].y < h1[5].y) currentFrameGesture = "天气";
        else if (indexUp && middleUp && !ringUp) currentFrameGesture = "吃";
        else if (getDist(h1[4], h1[8]) < 0.05) currentFrameGesture = "饭";
        else if (h1[9].y > 0.6) currentFrameGesture = "今天";
      }
    }

    // 防抖：连续多帧相同手势才确认（与组词效果/index.html一致）
    if (currentFrameGesture !== "" && currentFrameGesture === this.lastGestureName) {
      this.gestureCount++;
    } else {
      this.gestureCount = 0;
      this.lastGestureName = currentFrameGesture;
    }

    if (this.gestureCount >= this.DEBOUNCE_THRESHOLD) {
      this.triggerGesture(currentFrameGesture);
      this.gestureCount = 0;
    }
  }

  /**
   * 触发手势回调 - 与组词效果/index.html一致
   */
  triggerGesture(name) {
    if (this.gestureQueue[this.gestureQueue.length - 1] === name) return;
    this.gestureQueue.push(name);
    if (this.gestureQueue.length > this.QUEUE_MAX_LENGTH) this.gestureQueue.shift();
    
    // 更新锁定时间，防止过快重复触发
    this.lastGestureTime = Date.now();
    
    // 触发回调给script.js
    if (this.onGestureResult) {
      this.onGestureResult(name, this.gestureQueue);
    }
  }
}
