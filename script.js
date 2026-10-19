/* ===== 手语识别交互系统 V2 - 主逻辑 ===== */

const particleCanvas = document.getElementById('particle-canvas');
const ctx = particleCanvas.getContext('2d');
const doodleDisplay = document.getElementById('doodle-display');

let particles = [];
let animationId = null;
let isAssembling = false;

let wordQueue = [];
let currentDisplayWord = null;
let lastGestureWord = null;
let sentenceMatched = false;
let matchedSentenceKey = null;
let displayTimeout = null;
let sentenceTimeout = null;
let particleTimeout = null;

// ===== 组句表 =====
// 唯一权威来源：dataset/词汇组句表.md 第一节表格。
// 由 tools/build_sentences.py 生成 sentences.js（SENTENCES / SENTENCE_INFO / SENTENCE_META），
// 改句子只改 md 再重跑脚本，不要直接改下面的兜底表。
// 兜底表仅在 sentences.js 加载失败时使用，内容必须与 md 一致：
// 疑问句不写"吗/呢/句末的不"，疑问语气靠表情表达（md 第二节规则 6）。
const FALLBACK_SENTENCES = {
    "你,好": "你好",
    "今天,天气,好": "今天天气好",
    "你,吃,了": "你吃了吗",
    "你,喜欢,吃,苹果": "你喜欢吃苹果吗",
    "我,吃,苹果": "我吃苹果",
    "认识,你,很,高兴": "认识你很高兴",
    "我,很,高兴": "我很高兴",
    "我,回,家": "我回家",
    "我,喜欢,你": "我喜欢你",
    "我,爱,你": "我爱你",
    "我,吃,了,面包": "我吃了面包",
    "欢迎,来,我,家": "欢迎来我家",
    "我,想要,一,杯,咖啡": "我想要一杯咖啡",
    "我,想要,一,杯,水": "我想要一杯水",
    "厕所,在,哪里": "厕所在哪里",
    "等,公交车": "等公交车",
    "我,一直,在": "我一直在",
    "等,朋友": "等朋友",
    "早上,好": "早上好",
    "请,你,说,慢一点": "请你说慢一点",
    "我,可以,看见,你,的,声音": "我可以看见你的声音"
};

const SENTENCES_LOADED = !!(typeof SENTENCES !== 'undefined' && SENTENCES);
const comboMap = SENTENCES_LOADED ? SENTENCES : FALLBACK_SENTENCES;
// 词队列上限，与 md 第二节规则 2 同一个数（sentences.js 里由脚本写入）
const QUEUE_LIMIT = (typeof SENTENCE_META !== 'undefined' && SENTENCE_META.queueLimit) ? SENTENCE_META.queueLimit : 6;

if (!SENTENCES_LOADED) {
    console.warn('sentences.js 未加载，组句回退到 script.js 内置的 FALLBACK_SENTENCES');
}

// 键 -> md 里的展示信息（词序拆解 / 词编号 / 图片名）
function sentenceInfo(key) {
    return (typeof SENTENCE_INFO !== 'undefined' && SENTENCE_INFO[key]) ? SENTENCE_INFO[key] : null;
}

// 词序拆解：优先取 md 生成的 split 列，缺失时按键现推（两者结果一致）
function sentenceSplit(key) {
    const info = sentenceInfo(key);
    return info ? info.split : key.split(',').join(' + ');
}

// 队列还没凑满时，列出"以当前队列为前缀"的候选句，方便边打边核对词序
function pendingSentences() {
    const queueStr = wordQueue.join(',');
    if (!queueStr) return [];
    const hits = [];
    for (const key in comboMap) {
        if (key.startsWith(queueStr + ',')) hits.push(comboMap[key]);
    }
    return hits;
}

let corpusSentences = [];
const imageCache = {};

// 图片路径基础目录
const IMAGE_BASE = '组词效果';

// ===== 粒子类 =====
class Particle {
    constructor(x, y, color) {
        this.homeX = x;
        this.homeY = y;
        this.x = Math.random() * particleCanvas.width;
        this.y = Math.random() * particleCanvas.height;
        this.color = color;
        this.size = 3.5;
        this.vx = (Math.random() - 0.5) * 2;
        this.vy = (Math.random() - 0.5) * 2;
        this.ease = 0.18;
    }

    update() {
        if (isAssembling) {
            this.x += (this.homeX - this.x) * this.ease;
            this.y += (this.homeY - this.y) * this.ease;
        } else {
            this.x += this.vx;
            this.y += this.vy;
            if (this.x < 0 || this.x > particleCanvas.width) this.vx *= -1;
            if (this.y < 0 || this.y > particleCanvas.height) this.vy *= -1;
        }
    }

    draw() {
        ctx.fillStyle = this.color;
        ctx.fillRect(this.x, this.y, this.size, this.size);
    }
}

// ===== 粒子系统 =====
function initParticleSystem() {
    particleCanvas.width = window.innerWidth;
    particleCanvas.height = window.innerHeight;
    animate();
}

function animate() {
    ctx.clearRect(0, 0, particleCanvas.width, particleCanvas.height);
    ctx.globalAlpha = 1.0;
    particles.forEach(p => {
        p.update();
        p.draw();
    });
    animationId = requestAnimationFrame(animate);
}

function clearParticles() {
    particles = [];
    isAssembling = false;
}

// ===== 图片粒子化 =====
function initImageParticles(img) {
    const bufferCanvas = document.getElementById('buffer-canvas');
    const bCtx = bufferCanvas.getContext('2d');
    
    const maxWidth = window.innerWidth * 0.85;
    const maxHeight = window.innerHeight * 0.85;
    let w = img.width;
    let h = img.height;

    const ratio = Math.min(maxWidth / w, maxHeight / h);
    w = Math.floor(w * ratio);
    h = Math.floor(h * ratio);

    bufferCanvas.width = w;
    bufferCanvas.height = h;
    bCtx.clearRect(0, 0, w, h);
    bCtx.drawImage(img, 0, 0, w, h);

    const imageData = bCtx.getImageData(0, 0, w, h).data;
    particles = [];

    const step = 4;
    const offsetX = (window.innerWidth - w) / 2;
    const offsetY = (window.innerHeight - h) / 2;

    for (let y = 0; y < h; y += step) {
        for (let x = 0; x < w; x += step) {
            const index = (y * w + x) * 4;
            const r = imageData[index];
            const g = imageData[index + 1];
            const b = imageData[index + 2];
            const alpha = imageData[index + 3];

            if (alpha > 150) {
                const color = `rgb(${r}, ${g}, ${b})`;
                particles.push(new Particle(x + offsetX, y + offsetY, color));
            }
        }
    }
}

// ===== 文字粒子化 =====
function createTextParticles(text, color = '#ff3131') {
    clearParticles();
    
    const offCanvas = document.createElement('canvas');
    const offCtx = offCanvas.getContext('2d');
    
    const fontSize = text.length <= 2 ? 180 : (text.length <= 4 ? 140 : 100);
    offCtx.font = `bold ${fontSize}px "ZCOOL KuaiLe", "Microsoft YaHei", sans-serif`;
    offCtx.fillStyle = color;
    
    const textWidth = offCtx.measureText(text).width;
    offCanvas.width = Math.max(textWidth + 60, 300);
    offCanvas.height = fontSize + 60;
    
    offCtx.font = `bold ${fontSize}px "ZCOOL KuaiLe", "Microsoft YaHei", sans-serif`;
    offCtx.fillStyle = color;
    offCtx.textAlign = 'center';
    offCtx.textBaseline = 'middle';
    offCtx.fillText(text, offCanvas.width / 2, offCanvas.height / 2);
    
    const imageData = offCtx.getImageData(0, 0, offCanvas.width, offCanvas.height);
    const data = imageData.data;
    
    const centerX = particleCanvas.width / 2 - offCanvas.width / 2;
    const centerY = particleCanvas.height / 2 - offCanvas.height / 2;
    
    const step = 4;
    for (let y = 0; y < offCanvas.height; y += step) {
        for (let x = 0; x < offCanvas.width; x += step) {
            const i = (y * offCanvas.width + x) * 4;
            const alpha = data[i + 3];
            
            if (alpha > 100) {
                const r = data[i];
                const g = data[i + 1];
                const b = data[i + 2];
                const particleColor = `rgb(${r}, ${g}, ${b})`;
                const particle = new Particle(centerX + x, centerY + y, particleColor);
                particle.size = 4;
                particles.push(particle);
            }
        }
    }
    
    isAssembling = true;
}

// ===== 核心显示逻辑 - 图片粒子汇聚 =====
// 图片查找顺序：
//   1) IMAGE_DATA 内嵌 base64（data URL 不跨域，file:// 下也能 getImageData）
//   2) 组词效果/ 文件夹下的同名 png（http:// 下可用）
//   3) 都没有 → 文字粒子效果
function transformToImage(imgName) {
    try { fetch('/diag', { method: 'POST', body: JSON.stringify({ ev: 'show-image', name: imgName }), keepalive: true }).catch(() => {}); } catch (e) {}
    isAssembling = false;
    clearParticles();
    
    const baseName = imgName.replace('.png', '');
    
    // 1) 内嵌 base64
    if (typeof IMAGE_DATA !== 'undefined' && IMAGE_DATA[baseName]) {
        loadAndShowParticles(IMAGE_DATA[baseName], baseName);
        return;
    }

    // 2) 到 组词效果/ 文件夹查找同名图片
    loadAndShowParticles(IMAGE_BASE + '/' + baseName + '.png', baseName);
}

// 加载图片并做粒子汇聚；加载失败或画布被污染时回退文字粒子
function loadAndShowParticles(src, baseName) {
    const img = new Image();
    img.onload = () => {
        try {
            initImageParticles(img);
            isAssembling = true;
            clearTimeout(particleTimeout);
            particleTimeout = setTimeout(() => {
                isAssembling = false;
            }, 8000);
        } catch (e) {
            // file:// 下文件夹图片会污染画布，getImageData 抛 SecurityError
            console.warn('图片粒子失败，回退文字粒子:', e);
            createTextParticles(baseName);
        }
    };
    img.onerror = () => {
        console.warn('未找到图片 ' + src + '，使用文字粒子效果');
        createTextParticles(baseName);
    };
    img.src = src;
}

function hideImage() {
    doodleDisplay.style.opacity = '0';
    clearParticles();
    isAssembling = false;
    clearTimeout(particleTimeout);
}

// ===== 词队列管理 =====
function addWordToQueue(word) {
    if (wordQueue.length > 0 && wordQueue[wordQueue.length - 1] === word) return;
    
    wordQueue.push(word);
    if (wordQueue.length > QUEUE_LIMIT) {
        wordQueue.shift();
    }
    
    updateWordQueueUI();
    updateDataPanel();
    checkWordCombo();
}

function checkWordCombo() {
    const queueStr = wordQueue.join(',');
    let matched = false;
    
    for (let key in comboMap) {
        if (queueStr.endsWith(key)) {
            const sentence = comboMap[key];
            transformToImage(sentence + '.png');
            addLog(`句子匹配: ${sentence} ← ${sentenceSplit(key)}`);

            // 先记键再清队列，updateWordQueueUI() 才能把拆解留在面板上
            matchedSentenceKey = key;
            document.getElementById('sentence-match').textContent = sentence;
            sentenceMatched = true;
            
            wordQueue = [];
            updateWordQueueUI();
            updateDataPanel();
            
            clearTimeout(sentenceTimeout);
            sentenceTimeout = setTimeout(() => {
                hideImage();
                matchedSentenceKey = null;
                clearWordQueue();
                sentenceMatched = false;
                lastGestureWord = null;
                document.getElementById('sentence-match').textContent = '无';
                document.getElementById('current-gesture').textContent = '等待输入';
                addLog('效果消散，等待下一组输入');
            }, 8000);
            
            matched = true;
            break;
        }
    }
}

function clearWordQueue() {
    wordQueue = [];
    updateWordQueueUI();
    updateDataPanel();
}

function updateWordQueueUI() {
    const container = document.getElementById('word-queue');
    container.innerHTML = '';
    wordQueue.forEach(word => {
        const item = document.createElement('span');
        item.className = 'word-item';
        item.textContent = word;
        container.appendChild(item);
    });
    updateQueueSplitUI();
}

// 组句拆解展示（词序按 dataset/词汇组句表.md 的「词序拆解」列）
// 命中句子：显示"今天天气好 ＝ 今天 + 天气 + 好"；未命中：显示当前队列拆解 + 候选句
function updateQueueSplitUI() {
    const el = document.getElementById('word-queue-split');
    if (!el) return;

    if (matchedSentenceKey) {
        const info = sentenceInfo(matchedSentenceKey);
        const ids = info ? `（${info.ids}）` : '';
        el.textContent = `${comboMap[matchedSentenceKey]} ＝ ${sentenceSplit(matchedSentenceKey)}${ids}`;
        return;
    }

    if (wordQueue.length === 0) {
        el.textContent = '';
        return;
    }

    const candidates = pendingSentences();
    let hint = '';
    if (candidates.length > 0) {
        const shown = candidates.slice(0, 3).join(' / ');
        hint = ` ｜ 候选: ${shown}${candidates.length > 3 ? ' …' : ''}`;
    }
    el.textContent = `拆解: ${wordQueue.join(' + ')}${hint}`;
}

// ===== 手势识别回调 =====
function onGestureDetected(gesture) {
    console.log('[手势检测] onGestureDetected 被调用:', gesture);
    if (!gesture || gesture === '') {
        console.log('[手势检测] 手势为空，返回');
        return;
    }
    if (sentenceMatched) {
        console.log('[手势检测] 句子已匹配，忽略新手势');
        return;
    }
    // 注意：这里绝不能再按"与上一次相同的词"拦截。
    // 之前 `if (gesture === lastGestureWord) return;` 会让同一个手势只能出一次图，
    // 画面消失后再打一遍也永远没反应（lastGestureWord 只在句子匹配后才重置）。
    // 同一次动作的重复触发已由识别器内部的 armed 门闩 + consumeHistory 负责。
    lastGestureWord = gesture;
    console.log('[手势检测] 处理手势:', gesture);
    // 判断识别来源
    const isSLR500 = typeof SLR500_WORDS !== 'undefined' && SLR500_WORDS.includes(gesture);
    const BASIC_WORDS = ['我','你','好','不','很','喜欢','回','天气','吃','米饭','今天','爱','家','苹果','认识','高兴','了'];
    const source = (isSLR500 && !BASIC_WORDS.includes(gesture)) ? ' [SLR500]' : '';
    addLog(`识别到手势: ${gesture}${source}`);
    
    document.getElementById('current-gesture').textContent = gesture;
    
    const panelEl = document.getElementById('status-panel');
    panelEl.classList.add('pulse-effect');
    setTimeout(() => panelEl.classList.remove('pulse-effect'), 500);
    
    addWordToQueue(gesture);
    
    if (!sentenceMatched) {
        transformToImage(gesture + '.png');
    }
}

// ===== UI更新 =====
function updateDataPanel() {
    document.getElementById('queue-length').textContent = wordQueue.length;
    document.getElementById('status-indicator').textContent = 
        wordQueue.length > 0 ? 'ACTIVE' : 'IDLE';
}

function addLog(message) {
    const logEl = document.getElementById('log-stream');
    const time = new Date().toLocaleTimeString();
    logEl.innerHTML = `[${time}] ${message}<br>${logEl.innerHTML}`;
}

// ===== 静态判据实时读数（001–005）=====
// 手势不出图时，这行读数直接告诉你"最接近哪个词、还差哪一维"，不用再去翻 /tmp/sign_diag.log。
// 按 D 键显示/隐藏。
function initStaticHud(recognizer) {
    const el = document.getElementById('static-hud');
    if (!el) return;
    el.style.display = 'block';
    setInterval(() => {
        if (el.style.display === 'none') return;
        try {
            el.textContent = recognizer.staticHudText();
        } catch (e) {
            el.textContent = 'HUD 异常: ' + e.message;
        }
    }, 200);
    window.addEventListener('keydown', (e) => {
        if (e.key === 'd' || e.key === 'D') {
            el.style.display = (el.style.display === 'none') ? 'block' : 'none';
        }
        if (e.key === 'p' || e.key === 'P') {
            // 身体骨骼被自动停用（Pose 连续失败）后按 P 重试，不用刷新整页
            if (recognizer.retryPose && recognizer.retryPose()) {
                addLog('已重新启用身体骨骼（Pose）');
            } else {
                addLog('Pose 不可用：依赖没加载成功，请跑 bash tools/fetch_vendor.sh 后刷新');
            }
        }
    });
}

// ===== 加载corpus =====
async function loadCorpus() {
    try {
        const response = await fetch('corpus.txt');
        const text = await response.text();
        const lines = text.trim().split('\n');
        corpusSentences = lines.map(line => {
            const parts = line.split(/\s+/);
            return parts.length >= 2 ? parts.slice(1).join(' ') : '';
        }).filter(s => s.length > 0);
        console.log(`已加载 ${corpusSentences.length} 条corpus句子`);
        addLog(`已加载 ${corpusSentences.length} 条扩展句子`);
    } catch (e) {
        console.warn('corpus.txt加载失败:', e);
    }
}

// ===== 预加载图片（从IMAGE_DATA嵌入的base64数据）=====
function preloadImages() {
    if (typeof IMAGE_DATA === 'undefined') {
        console.warn('IMAGE_DATA未加载，图片粒子效果不可用');
        return;
    }
    
    const allNames = Object.keys(IMAGE_DATA);
    let loaded = 0;
    allNames.forEach(name => {
        const img = new Image();
        img.onload = () => { imageCache[name] = img; loaded++; };
        img.src = IMAGE_DATA[name];
    });
    console.log(`开始预加载 ${allNames.length} 张嵌入图片...`);
    setTimeout(() => {
        console.log(`[预加载] 完成: 成功 ${loaded} 张`);
    }, 2000);
}

// ===== 初始化 =====
async function init() {
    console.log('手语识别交互系统 V3 启动中...');

    // MediaPipe 现在是异步加载的（本地 vendor 优先、CDN 兜底，见 mp-loader.js）。
    // 必须等它落地再 new Hands/Pose：否则 DOMContentLoaded 触发时 Hands 还没定义，
    // 会直接抛"MediaPipe Hands 未加载"，页面永远黑着。
    if (window.mpReady) {
        try { await window.mpReady; } catch (e) { console.warn('MediaPipe 依赖加载异常:', e); }
    }
    
    // 检查SLR500模板数据
    if (typeof SLR500_WORDS !== 'undefined') {
        console.log(`SLR500模板已加载: ${SLR500_WORDS.length} 个词汇`);
        addLog(`SLR500词库已加载: ${SLR500_WORDS.length} 个词汇`);
    } else {
        console.warn('SLR500模板数据未加载');
    }
    
    // 组句表状态（来源：dataset/词汇组句表.md → sentences.js）
    const sentenceCount = Object.keys(comboMap).length;
    if (SENTENCES_LOADED && typeof SENTENCE_META !== 'undefined') {
        console.log(`组句表已加载: ${sentenceCount} 句（来源 ${SENTENCE_META.source}，最长 ${SENTENCE_META.maxWords} 词，队列上限 ${QUEUE_LIMIT}）`);
        addLog(`组句表: ${sentenceCount} 句 ← ${SENTENCE_META.source}`);
    } else {
        addLog(`组句表: ${sentenceCount} 句（sentences.js 未加载，用内置兜底表）`);
    }

    initParticleSystem();
    preloadImages();
    await loadCorpus();
    
    const videoEl = document.getElementById('webcam');
    const canvasEl = document.getElementById('output-canvas');
    
    const recognizer = new GestureRecognizer();
    recognizer.onGestureResult = onGestureDetected;
    window.recognizer = recognizer;
    initStaticHud(recognizer);


    recognizer.onError = (stage, message) => {
        addLog(`${stage} 异常: ${message}`);
        if (typeof window.__diag === 'function') window.__diag(`${stage}: ${message}`);
    };
    
    try {
        await recognizer.init(videoEl, canvasEl);
        
        setTimeout(() => {
            const loadingEl = document.getElementById('loading-screen');
            loadingEl.style.opacity = '0';
            setTimeout(() => loadingEl.style.display = 'none', 500);
            addLog('系统启动完成，请开始做手势');
        }, 1000);
        
    } catch (e) {
        console.error('手势识别初始化失败:', e);
        const loadingEl = document.getElementById('loading-screen');
        if (typeof window.__diag === 'function') window.__diag(e.message);
        loadingEl.innerHTML = `
            <div class="loading-content">
                <div class="loading-text" style="color: #e74c3c">初始化失败</div>
                <div class="loading-subtext">${e.message}</div>
                <div class="loading-subtext" style="margin-top: 20px">请确保允许摄像头权限，并通过 http://localhost:8080 访问</div>
            </div>
        `;
    }
    
    window.addEventListener('resize', () => {
        particleCanvas.width = window.innerWidth;
        particleCanvas.height = window.innerHeight;
    });
}

// 用 DOMContentLoaded 而非 load：
// load 会等待字体、图片等全部子资源就绪；一旦 fonts.googleapis.com 或 CDN 挂起，
// init() 就永远不会执行，表现为“无骨骼、无识别”。
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
