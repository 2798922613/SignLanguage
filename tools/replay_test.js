// 运行方式：osascript -l JavaScript tools/replay_test.js <mode> [参数...]
/**
 * 离线回放回归测试 —— 不用摄像头、不用浏览器，直接把 dataset/video/*.json 的骨骼
 * 按原始时间戳喂给真实的 GestureRecognizer，检查"改了阈值以后原来能识别的词还认不认得"。
 *
 * 为什么需要它：每词只有 1 条录制时，实时域差会让 DTW 绝对距离必然超标，判据只能靠
 * "手停住时长什么样"的比值特征（见 gesture-recognizer.js 的 STATIC_RULES）。这些阈值
 * 一改就可能把别的词挤掉，所以每次加词/调阈值都要跑一遍本脚本。
 *
 * 用法（在仓库根目录执行）：
 *   osascript -l JavaScript tools/replay_test.js all          # 标准回归（推荐，加词后必跑）
 *   osascript -l JavaScript tools/replay_test.js words        # 列出录制文件与词库覆盖情况
 *   osascript -l JavaScript tools/replay_test.js single       # 每段单独回放：只出正确词、且只出一次
 *   osascript -l JavaScript tools/replay_test.js runs         # 连续命中片段：看过渡期串词有多长
 *   osascript -l JavaScript tools/replay_test.js hold         # 最短保持时长扫描（定 STATIC_HOLD_MS 用）
 *   osascript -l JavaScript tools/replay_test.js foreign      # 未进词库的录制回放：一个图都不该出
 *   osascript -l JavaScript tools/replay_test.js seq '{}' 30 我,你,今天,天气,好
 *   osascript -l JavaScript tools/replay_test.js rob '{}' 0 2 0.006 1 0 7 0.75
 *   osascript -l JavaScript tools/replay_test.js morph '{}' 0 我 1.5,1.5,1.5,1.5,1.5
 *   osascript -l JavaScript tools/replay_test.js feat '{}' 30 你_s01_002
 *   osascript -l JavaScript tools/replay_test.js hud 你_s01_002 2.0
 *
 * 各模式参数：
 *   single [OVR] [TAIL]                 TAIL = 播完后再喂多少帧"手冻在画面里"（默认 30）
 *   seq    [OVR] [TAIL] [词序,逗号分隔]  词间自动插入 300/800/1500ms 的"手放下"间隔
 *   rob    [OVR] [TAIL] [DROP] [NOISE] [SCALE] [DY] [SEED] [AR]
 *          DROP=每几帧取 1 帧（2≈15fps）、NOISE=landmark 高斯噪声 σ、SCALE=整体缩放（远近）、
 *          DY=机位垂直平移、AR=宽高比（0.5625=16:9、0.75=4:3）
 *   morph  [OVR] [TAIL] [词] [k拇,k食,k中,k无名,k小]   k>1=把该手指摆直、k<1=蜷起、k=1=不动
 *          期望：改造成"非词库手形"后不再命中原词
 *   feat   [OVR] [TAIL] [文件名]         逐帧打印静态判据特征（1/4 采样），调阈值用
 *   hud    [文件名] [秒]                  打印页面右下角 HUD 在那个时刻的文本
 *
 * OVR 是 JSON，直接覆写识别器实例属性，例如 '{"STATIC_HOLD_MS":300}'、'{"STATIC_ENABLE":false}'。
 * 仓库根目录：默认取当前工作目录；也可用环境变量 SL_ROOT 指定。
 */

ObjC.import('Foundation');

// ===== 虚拟时钟：识别器内部全部用 Date.now()，回放时要按录制时间戳走 =====
var VNOW = 0;
var Date = { now: function () { return VNOW; } };
var localStorage = { getItem: function () { return null; }, setItem: function () {} };
var console = { log: function () {}, warn: function () {}, error: function () {} };
var window = {};
var fetch = undefined;

function readFile(p) {
  try { return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null)); }
  catch (e) { return null; }
}
function listDir(p) {
  var arr = $.NSFileManager.defaultManager.contentsOfDirectoryAtPathError(p, null);
  if (!arr) return [];
  var out = [], n = arr.count;
  for (var i = 0; i < n; i++) out.push(ObjC.unwrap(arr.objectAtIndex(i)));
  return out;
}
function resolveRoot() {
  try {
    var env = $.NSProcessInfo.processInfo.environment.objectForKey('SL_ROOT');
    if (env) {
      var p = ObjC.unwrap(env);
      if (p && readFile(p + '/gesture-recognizer.js')) return p;
    }
  } catch (e) { /* 取不到环境变量就用 cwd */ }
  var cwd = ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
  if (cwd && readFile(cwd + '/gesture-recognizer.js')) return cwd;
  return null;
}
var ROOT = resolveRoot();

// ===== 加载词库与识别器（const/let → var，否则 eval 出来 run() 里取不到）=====
function toVar(s) { return s.replace(/^const /gm, 'var ').replace(/^let /gm, 'var '); }
(function load() {
  var tpl = readFile(ROOT + '/自建-templates.js');
  var gr = readFile(ROOT + '/gesture-recognizer.js');
  if (!tpl || !gr) throw new Error('找不到 自建-templates.js / gesture-recognizer.js，ROOT=' + ROOT);
  eval(toVar(tpl));
  eval(toVar(gr) + '\n;globalThis.GestureRecognizer = GestureRecognizer;' +
    'try{globalThis.__RULES = STATIC_RULES.map(function(r){return r.w;});}catch(e){globalThis.__RULES = [];}' +
    'try{globalThis.__RULES2 = STATIC_RULES2.map(function(r){return r.w;});}catch(e){globalThis.__RULES2 = [];}' +
    'try{globalThis.__SKEYS = STATIC_KEYS.slice();}catch(e){globalThis.__SKEYS = [];}' +
    'try{globalThis.__PKEYS = STATIC_PAIR_KEYS.slice();}catch(e){globalThis.__PKEYS = [];}' +
    'try{globalThis.__SR = STATIC_RULES; globalThis.__SR2 = STATIC_RULES2; globalThis.__OPS = STATIC_OPS;}catch(e){}');
})();

// ===== 录制文件发现：dataset/video/<词>_<说话人>_<编号>.json =====
// 词取文件名第一段，斜杠/冒号前的是主词（与 tools/build_dataset.py 的 parse_name() 同口径）
function discover() {
  var dir = ROOT + '/dataset/video';
  return listDir(dir).filter(function (n) { return /\.json$/.test(n); })
    .map(function (n) {
      var stem = n.replace(/\.json$/, '');
      var p = stem.split('_');
      var raw = p[0] || stem;
      var word = raw.split(/[:\/]/)[0];
      return { stem: stem, file: n, raw: raw, word: word,
               speaker: p[1] || '', index: p[2] || '' };
    })
    .sort(function (a, b) { return (a.index + a.stem) < (b.index + b.stem) ? -1 : 1; });
}
var CACHE = {};
function load(stem) {
  if (CACHE[stem]) return CACHE[stem];
  var d = JSON.parse(readFile(ROOT + '/dataset/video/' + stem + '.json'));
  if (!d || !d.frames || !d.frames.length) throw new Error('读取失败或没有帧: ' + stem);
  return (CACHE[stem] = { d: d, f: d.frames, t0: d.frames[0].t, stem: stem });
}

// ===== 识别器实例与喂帧 =====
function convHands(h) {
  if (!h || !h.length) return [];
  return h.map(function (x) { return x.pts.map(function (p) { return { x: p[0], y: p[1], z: p[2] }; }); });
}
function convPose(p) {
  if (!p) return null;
  return p.map(function (q) { return { x: q[0], y: q[1], z: q[2], visibility: q[3] }; });
}
function mk(OVR) {
  var rec = new GestureRecognizer();
  for (var k in OVR) rec[k] = OVR[k];
  rec.reportDiag = function () {};
  rec.trig = [];
  rec._t0 = 0;
  rec.onGestureResult = function (w) { rec.trig.push({ w: w, t: VNOW - rec._t0 }); };
  return rec;
}
function feed(rec, f, t) {
  VNOW = t;
  rec.latestPoseResults = f.pose ? { poseLandmarks: convPose(f.pose) } : null;
  rec.latestPoseT = VNOW;
  rec.analyzeGestures({ multiHandLandmarks: convHands(f.hands) }, null);
}
function trigStr(rec) {
  return rec.trig.map(function (x) { return x.w + '@' + (x.t / 1000).toFixed(2) + 's'; }).join(', ');
}

// 本帧判据命中了哪个词（不含保持/静止门）——与 matchStaticWords 的选词逻辑严格一致：
// 双手判据优先（009 认识），否则单手判据；两只手命中不同词算歧义 → 不判。
function curWord(rec) {
  if (rec.stPairWord) return rec.stPairWord;
  var hits = [];
  for (var k in rec.stSmooth) {
    var s = rec.stSmooth[k];
    var w = rec.staticRule(s);
    if (w) hits.push({ w: w, pl: s.pl || 0 });
  }
  if (hits.length === 1) return hits[0].w;
  if (hits.length > 1) {
    hits.sort(function (a, b) { return b.pl - a.pl; });
    if (hits[0].w === hits[1].w) return hits[0].w;
  }
  return '';
}
function curFeat(rec) {
  var best = null;
  for (var k in rec.stSmooth) {
    var s = rec.stSmooth[k];
    if (!best || (s.pl || 0) > (best.pl || 0)) best = s;
  }
  return best;
}

// 本帧"某个指定词"还差哪几维（单手表 + 双手表都查）——扰动模式失败时靠它直接说出原因
function missFor(rec, word) {
  var f = curFeat(rec), pf = rec.stPairFeat, miss = [];
  var chk = function (rules, src) {
    (rules || []).forEach(function (r) {
      if (r.w !== word || !src) return;
      r.c.forEach(function (c) {
        if (!globalThis.__OPS[c[1]](src[c[0]], c[2]))
          miss.push(c[0] + c[1] + c[2] + '(实' + (+src[c[0]]).toFixed(2) + ')');
      });
    });
  };
  chk(globalThis.__SR, f);
  chk(globalThis.__SR2, pf);
  return miss;
}
// 该词是否已进识别器词库（没进就不可能出图，测试要跳过而不是判失败）
function inLib(word) {
  var sw = (new GestureRecognizer()).STATIC_WORDS || [];
  var cw = (typeof CUSTOM_WORDS !== 'undefined') ? CUSTOM_WORDS : [];
  return sw.indexOf(word) >= 0 || cw.indexOf(word) >= 0;
}

// ===== 回放：stopAt 用于"只喂到某时刻"（测最短保持时长）=====
function replay(stem, OVR, TAIL, stopAt) {
  var F = load(stem), rec = mk(OVR || {});
  rec._t0 = F.t0;
  var lastFull = null;
  for (var i = 0; i < F.f.length; i++) {
    var f = F.f[i];
    if (stopAt != null && f.t > stopAt) break;
    if (f.pose) lastFull = f;
    feed(rec, f, F.t0 + f.t);
  }
  var tEnd = F.t0 + F.f[F.f.length - 1].t;
  if (stopAt == null) {
    for (var j = 1; j <= (TAIL || 0); j++) {
      feed(rec, { pose: lastFull ? lastFull.pose : null, hands: lastFull ? lastFull.hands : [] }, tEnd + j * 33);
    }
  }
  return rec;
}

// ===== 连续命中片段（含过渡期串词）：用来定 STATIC_HOLD_MS 下限 =====
function runsOf(stem, OVR) {
  var F = load(stem), rec = mk(OVR || {}), out = [], cur = '', ct0 = 0, pt = 0;
  rec._t0 = F.t0;
  for (var i = 0; i < F.f.length; i++) {
    var f = F.f[i];
    feed(rec, f, F.t0 + f.t);
    var w = curWord(rec);
    if (w !== cur) { if (cur) out.push({ w: cur, t0: ct0, t1: pt }); cur = w; ct0 = f.t; }
    pt = f.t;
  }
  if (cur) out.push({ w: cur, t0: ct0, t1: pt });
  return out;
}

// ===== 各模式 =====
function modeWords(out) {
  var rec = mk({});
  out.push('ROOT = ' + ROOT);
  out.push('静态判据词（STATIC_RULES）: ' + (globalThis.__RULES || []).join('、'));
  out.push('双手判据词（STATIC_RULES2）: ' + ((globalThis.__RULES2 || []).join('、') || '(无)'));
  out.push('STATIC_WORDS: ' + (rec.STATIC_WORDS || []).join('、') +
           '   HOLD=' + rec.STATIC_HOLD_MS + 'ms  STILL_SW=' + rec.STATIC_STILL_SW +
           '  TAU=' + rec.STATIC_TAU + 'ms');
  out.push('CUSTOM_WORDS(DTW): ' + ((typeof CUSTOM_WORDS !== 'undefined') ? CUSTOM_WORDS.join('、') : '(无)'));
  out.push('');
  out.push('| 文件 | 词 | 编号 | 说话人 | 时长 | 帧数 | 已进词库 |');
  out.push('|---|---|---|---|---|---|---|');
  discover().forEach(function (r) {
    var F = load(r.stem);
    out.push('| ' + r.file + ' | ' + r.word + ' | ' + r.index + ' | ' + r.speaker + ' | ' +
      (F.d.durationMs / 1000).toFixed(2) + 's | ' + F.f.length + ' | ' + (inLib(r.word) ? '✅' : '⬜ 未进') + ' |');
  });
}

function modeSingle(OVR, TAIL, out, quiet) {
  var ok = 0, n = 0, skip = [];
  discover().forEach(function (r) {
    if (!inLib(r.word)) { skip.push(r.word); return; }
    n++;
    var rec = replay(r.stem, OVR, TAIL);
    var good = (rec.trig.length === 1 && rec.trig[0].w === r.word);
    if (good) ok++;
    out.push((good ? '✓' : '✗') + ' ' + r.word + '（' + r.stem + '）触发=[' + trigStr(rec) + ']');
  });
  out.push('单段回放 ' + ok + '/' + n + (skip.length ? '   跳过（未进词库）: ' + skip.join('、') : '') +
           '   TAIL=' + TAIL);
  return { ok: ok, n: n };
}

function modeSeq(OVR, TAIL, order, out) {
  var list = String(order || '').split(',').map(function (s) { return s.trim(); }).filter(function (w) { return w; });
  var byWord = {};
  discover().forEach(function (r) { if (!byWord[r.word]) byWord[r.word] = r.stem; });
  list = list.filter(function (w) {
    if (!byWord[w]) { out.push('⚠ 没有该词的录制，已跳过: ' + w); return false; }
    return true;
  });
  if (!list.length) { out.push('seq: 没有可用词'); return { ok: 0, n: 0 }; }
  var ok = 0, n = 0;
  [300, 800, 1500].forEach(function (gap) {
    var rec = mk(OVR || {}), T = 0;
    rec._t0 = 0;
    list.forEach(function (w) {
      var F = load(byWord[w]), lastFull = null;
      for (var i = 0; i < F.f.length; i++) { var f = F.f[i]; if (f.pose) lastFull = f; feed(rec, f, T + f.t); }
      T += F.d.durationMs + gap;
      for (var k = 0; k < Math.round(gap / 33); k++) {
        feed(rec, { pose: lastFull ? lastFull.pose : null, hands: [] }, T - gap + k * 33);
      }
    });
    var seq = rec.trig.map(function (x) { return x.w; }).join('+');
    var good = (seq === list.join('+'));
    if (good) ok++;
    n++;
    out.push((good ? '✓' : '✗') + ' 间隔' + gap + 'ms 期望[' + list.join('+') + '] 实际[' + seq + '] ' +
      rec.trig.map(function (x) { return (x.t / 1000).toFixed(1) + 's'; }).join(','));
  });
  return { ok: ok, n: n };
}

function modeRob(OVR, DROP, NOISE, SCALE, DY, SEED, ASP, out, ZS) {
  var st = SEED;
  function rnd() { st = (st * 1103515245 + 12345) & 0x7fffffff; return st / 0x7fffffff; }
  function g() { return (rnd() + rnd() + rnd() + rnd() - 2) * 0.7; }
  var ok = 0, n = 0, det = [], fails = [];
  discover().forEach(function (r) {
    if (!inLib(r.word)) return;
    n++;
    var F = load(r.stem), rec = mk(OVR || {});
    rec._t0 = F.t0;
    var why = {}, bestRun = 0, runMs = 0, runT0 = -1;
    var maxAge = 0, maxDriftLate = 0;
    if (ASP > 0) rec.videoEl = { videoWidth: 1000, videoHeight: Math.round(1000 * ASP) };
    var KY = ASP > 0 ? (0.5625 / ASP) : 1;
    function P(q) {
      var x = 0.5 + (q[0] - 0.5) * SCALE, y = DY + 0.5 + (q[1] - 0.5) * SCALE;
      if (KY !== 1) y = 0.5 + (y - 0.5) * KY;
      return [x + (NOISE ? g() * NOISE : 0), y + (NOISE ? g() * NOISE : 0),
              q[2] * ZS + (NOISE ? g() * NOISE : 0), q[3]];
    }
    for (var i = 0; i < F.f.length; i++) {
      if (DROP > 1 && (i % DROP) !== 0) continue;
      var f = F.f[i], pose = null, hands = [];
      if (f.pose) pose = f.pose.map(P);
      (f.hands || []).forEach(function (H) { hands.push({ label: H.label, score: H.score, pts: H.pts.map(P) }); });
      feed(rec, { pose: pose, hands: hands }, F.t0 + f.t);
      var cw = curWord(rec);
      if (cw === r.word) {
        runMs = f.t - (runT0 < 0 ? f.t : runT0); if (runT0 < 0) runT0 = f.t;
        if (rec.stWord === r.word && rec.stSince) {
          var age = (F.t0 + f.t) - rec.stSince;
          if (age > maxAge) maxAge = age;
          if (age >= rec.STATIC_HOLD_MS && rec.stDrift > maxDriftLate) maxDriftLate = rec.stDrift;
        }
      }
      else { if (runMs > bestRun) bestRun = runMs; runT0 = -1; runMs = 0;
             missFor(rec, r.word).forEach(function (m) { why[m] = (why[m] || 0) + 1; }); }
    }
    if (runMs > bestRun) bestRun = runMs;
    var good = (rec.trig.length === 1 && rec.trig[0].w === r.word);
    if (good) ok++;
    det.push((good ? '✓' : '✗') + r.word + '[' + rec.trig.map(function (x) { return x.w + '@' + (x.t / 1000).toFixed(1); }).join(',') + ']');
    if (!good) {
      var top = Object.keys(why).sort(function (a, b) { return why[b] - why[a]; }).slice(0, 3)
        .map(function (k) { return k + '×' + why[k]; });
      fails.push('    ↳ ' + r.word + ' 最长连续命中 ' + bestRun + 'ms（门槛 ' + rec.STATIC_HOLD_MS +
        'ms）；识别器内部最长 stSince ' + maxAge + 'ms、达标后最大漂移 ' + maxDriftLate.toFixed(2) +
        '（门槛 ' + rec.STATIC_STILL_SW + '）；被挡住最多的维度：' + (top.join('、') || '无'));
    }
  });
  out.push('丢帧1/' + DROP + ' σ=' + NOISE + ' 缩放=' + SCALE + ' Δy=' + DY + ' AR=' + (ASP || 0.5625) +
    ' 缩放z=' + ZS + ' seed=' + SEED + ' → ' + ok + '/' + n + '  ' + det.join(' '));
  fails.forEach(function (x) { out.push(x); });
  return { ok: ok, n: n };
}

function modeMorph(OVR, word, KS, DBG, out) {
  var rec0 = null, stem = null;
  discover().forEach(function (r) { if (r.word === word && !stem) { stem = r.stem; rec0 = r; } });
  if (!stem) { out.push('morph: 没有词 "' + word + '" 的录制'); return; }
  var F = load(stem);
  var CH = [[2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]];
  var rec = mk(OVR || {});
  rec._t0 = F.t0;
  for (var i = 0; i < F.f.length; i++) {
    var f = F.f[i], hands = [];
    (f.hands || []).forEach(function (H) {
      var pts = H.pts.map(function (p) { return p.slice(); });
      for (var fi = 0; fi < 5; fi++) {
        var k = KS[fi];
        if (k === 1) continue;
        var idx = (fi === 0) ? [1, 2, 3, 4] : CH[fi];
        var m = pts[idx[0]], L = [], tot = 0;
        for (var q = 1; q < idx.length; q++) {
          var a = pts[idx[q - 1]], b = pts[idx[q]];
          var dd = Math.sqrt(Math.pow(a[0] - b[0], 2) + Math.pow((a[1] - b[1]) * 0.5625, 2) + Math.pow(a[2] - b[2], 2));
          L.push(dd); tot += dd;
        }
        var tp = pts[idx[idx.length - 1]];
        var dx = tp[0] - m[0], dy = (tp[1] - m[1]) * 0.5625, dz = tp[2] - m[2];
        var dl = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-6;
        dx /= dl; dy /= dl; dz /= dl;
        if (k >= 1) {           // 摆直：三节指骨沿 mcp→tip 排成直线（ext → 1.00）
          var acc = 0;
          for (var q2 = 1; q2 < idx.length; q2++) {
            acc += L[q2 - 1];
            pts[idx[q2]] = [m[0] + dx * acc, m[1] + dy * acc / 0.5625, m[2] + dz * acc];
          }
        } else {                 // 蜷起：每节指骨绕掌法线递进旋转（真的弯，不是整根缩放）
          var ax = pts[5][0] - pts[0][0], ay = (pts[5][1] - pts[0][1]) * 0.5625, az = pts[5][2] - pts[0][2];
          var bx = pts[17][0] - pts[0][0], by = (pts[17][1] - pts[0][1]) * 0.5625, bz = pts[17][2] - pts[0][2];
          var nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
          var nl = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1e-9;
          nx /= nl; ny /= nl; nz /= nl;
          var dot = nx * dx + ny * dy + nz * dz;
          nx -= dx * dot; ny -= dy * dot; nz -= dz * dot;   // 与 dir 正交化，否则弯曲会把指骨拉长
          nl = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1e-9;
          nx /= nl; ny /= nl; nz /= nl;
          var per = (1 - k) * 1.15, px = m[0], py = m[1], pz = m[2];
          for (var q3 = 1; q3 < idx.length; q3++) {
            var th = per * (q3 - 1), ct = Math.cos(th), sn = Math.sin(th);
            var sx = dx * ct + nx * sn, sy = dy * ct + ny * sn, sz = dz * ct + nz * sn;
            px += sx * L[q3 - 1]; py += (sy * L[q3 - 1]) / 0.5625; pz += sz * L[q3 - 1];
            pts[idx[q3]] = [px, py, pz];
          }
        }
      }
      hands.push({ label: H.label, score: H.score, pts: pts });
    });
    feed(rec, { pose: f.pose, hands: hands }, F.t0 + f.t);
    if (DBG && i % 12 === 0) {
      var sm = curFeat(rec);
      if (sm) out.push('  t=' + (f.t / 1000).toFixed(2) + ' rule=' + (curWord(rec) || '-') +
        ' mn4=' + sm.mn4.toFixed(2) + ' mx3=' + sm.mx3.toFixed(2) + ' ext1=' + sm.ext1.toFixed(2) +
        ' ext2=' + sm.ext2.toFixed(2) + ' thAt5=' + sm.thAt5.toFixed(2) + ' mdI=' + sm.mdI.toFixed(2) +
        ' dx1=' + sm.dx1.toFixed(2) + ' dy1=' + sm.dy1.toFixed(2) +
        ' dziR=' + sm.dziR.toFixed(2) + ' hy=' + sm.hy.toFixed(2) + ' nd=' + sm.nd.toFixed(2) +
        ' ny=' + sm.ny.toFixed(2) + ' tiR=' + sm.tiR.toFixed(2));
    }
  }
  out.push('改造 ' + word + ' k=[' + KS.join(',') + '] → 触发 [' + trigStr(rec) + ']');
}

function modeFeat(OVR, stem, out) {
  var F = load(stem), rec = mk(OVR || {});
  rec._t0 = F.t0;
  var K1 = (globalThis.__SKEYS || []).filter(function (k) { return k !== 'hx' && k !== 'txh' && k !== 'tyh'; });
  for (var i = 0; i < F.f.length; i++) {
    var f = F.f[i];
    feed(rec, f, F.t0 + f.t);
    if (i % 4) continue;
    var s = curFeat(rec);
    if (!s) continue;
    out.push('t=' + (f.t / 1000).toFixed(2) + ' w=' + (curWord(rec) || '-') + ' ' +
      K1.map(function (k) { return k + '=' + s[k].toFixed(2); }).join(' ') +
      ' drift=' + rec.stDrift.toFixed(2) + ' since=' + (rec.stSince ? VNOW - rec.stSince : 0));
    var pf = rec.stPairFeat;
    if (pf) out.push('         ‖双手 ' +
      (globalThis.__PKEYS || []).map(function (k) { return k + '=' + pf[k].toFixed(2); }).join(' '));
  }
}

function modeRuns(OVR, out) {
  var worst = 0, worstTxt = '';
  var foreign = [];
  discover().forEach(function (r) {
    var inlib = inLib(r.word);
    var rs = runsOf(r.stem, OVR).filter(function (x) { return x.w; });
    var txt = (rs.map(function (x) {
      return x.w + '[' + (x.t0 / 1000).toFixed(2) + '→' + (x.t1 / 1000).toFixed(2) + 's/' + (x.t1 - x.t0) + 'ms]';
    }).join('  ') || '（从未命中）');
    out.push((inlib ? '' : '⬜ ') + r.word + '（' + r.stem + '）连续命中：' + txt);
    if (inlib) {
      rs.forEach(function (x) {
        if (x.w !== r.word && (x.t1 - x.t0) > worst) { worst = x.t1 - x.t0; worstTxt = r.word + ' 录制里的 ' + x.w; }
      });
    } else if (rs.length) {
      foreign.push(r.word);
    }
  });
  var rec = mk(OVR || {});
  out.push('→ 过渡期串词最长 ' + worst + 'ms（' + (worstTxt || '无') + '），STATIC_HOLD_MS=' + rec.STATIC_HOLD_MS +
    'ms ' + (worst < rec.STATIC_HOLD_MS ? '✅ 能滤掉' : '❌ 会误触发，请调大 HOLD'));
  if (foreign.length) {
    out.push('⚠ 这些"还没进词库"的录制也命中了判据（现场做这些手势可能蹦出旧词），跑 foreign 模式看是否真的出图：' +
      foreign.join('、'));
  }
  return worst < rec.STATIC_HOLD_MS ? { ok: 1, n: 1 } : { ok: 0, n: 1 };
}

// ===== 未进词库的录制：现场做这些手势时，一个图都不该出 =====
// 为什么要测：判据看的是"手停住时长什么样"，词库外的手势（如 008 了 = 五指张开、010 很 = 食指横伸）
// 很容易撞上已有词的判据。这类误触发只有把全部录制都回放一遍才暴露得出来。
function modeForeign(OVR, out) {
  var ok = 0, n = 0;
  discover().forEach(function (r) {
    if (inLib(r.word)) return;
    n++;
    var rec = replay(r.stem, OVR, 0);
    var good = (rec.trig.length === 0);
    if (good) ok++;
    out.push((good ? '✓' : '❌') + ' ' + r.word + '（' + r.stem + '，未进词库）触发=[' + (trigStr(rec) || '无') + ']');
  });
  out.push(n ? '词库外录制 ' + ok + '/' + n + ' 无误触发' : '词库外录制：无（全部已进词库）');
  return { ok: ok, n: n };
}

function modeHold(OVR, out) {
  var XS = [250, 300, 400, 500, 600, 800, 1200];
  discover().forEach(function (r) {
    if (!inLib(r.word)) return;
    var F = load(r.stem), probe = mk(OVR || {}), firstHit = -1;
    probe._t0 = F.t0;
    for (var i = 0; i < F.f.length; i++) {
      var f = F.f[i];
      feed(probe, f, F.t0 + f.t);
      if (firstHit < 0 && curWord(probe) === r.word) firstHit = f.t;
    }
    if (firstHit < 0) { out.push(r.word + ': 该段从未命中判据 ❌'); return; }
    var line = r.word + ' 首次命中@' + (firstHit / 1000).toFixed(2) + 's → ', first = -1;
    XS.forEach(function (X) {
      var rec = replay(r.stem, OVR, 0, firstHit + X);
      var good = (rec.trig.length === 1 && rec.trig[0].w === r.word);
      if (good && first < 0) first = X;
      line += X + ':' + (good ? '✓' : (rec.trig.length ? '✗' + rec.trig[0].w : '✗')) + '  ';
    });
    out.push(line + (first > 0 ? '→ 最短约 ' + first + 'ms' : '→ 1.2s 内不出图 ❌'));
  });
}

function modeHud(stem, at, out) {
  var F = load(stem), rec = mk({}), last = '(该时刻没有特征)';
  rec._t0 = F.t0;
  for (var i = 0; i < F.f.length; i++) {
    var f = F.f[i];
    feed(rec, f, F.t0 + f.t);
    if (Math.abs(f.t / 1000 - at) < 0.05) last = rec.staticHudText();
  }
  out.push(stem + ' t≈' + at + 's 的 HUD：');
  out.push('  ' + last);
  out.push('触发：[' + trigStr(rec) + ']');
}

// ===== 标准回归：加词/改阈值后跑这一条就够 =====
function modeAll(OVR, out) {
  var sum = { ok: 0, n: 0 };
  var add = function (r) { sum.ok += r.ok; sum.n += r.n; };
  var words = discover().filter(function (r) { return inLib(r.word); }).map(function (r) { return r.word; });
  out.push('═══ 1/6 单段回放（TAIL=0，不靠"录完后手冻在画面里"）═══');
  add(modeSingle(OVR, 0, out));
  out.push('');
  out.push('═══ 2/6 单段回放（TAIL=30）═══');
  add(modeSingle(OVR, 30, out));
  out.push('');
  out.push('═══ 3/6 组句序列（含重复词与易混对相邻）═══');
  var orders = [];
  if (words.length) {
    orders.push(words.join(','));
    orders.push(words.slice().reverse().join(','));
    orders.push([words[0], words[0], words[0]].join(','));
    if (words.length >= 2) orders.push([words[0], words[1], words[0], words[1], words[0]].join(','));
    orders.push(words.slice().sort().join(','));
  }
  orders.forEach(function (o) {
    out.push('— 词序 ' + o);
    add(modeSeq(OVR, 30, o, out));
  });
  out.push('');
  out.push('═══ 4/6 扰动复测 ═══');
  //          DROP NOISE SCALE DY   SEED AR   ZS
  // ZS=1（默认）= 只缩 x/y、z 不动 → 模拟"z 标定偏差 / 深度噪声被相对放大"，比真实情况更狠；
  // ZS=SCALE     = x/y/z 同比缩放 → 真实的"人离镜头远/近"（相似变换，比值特征本该完全不变）。
  var M = [[1, 0, 1, 0, 101, 0, 1], [2, 0, 1, 0, 102, 0, 1], [3, 0, 1, 0, 103, 0, 1],
           [1, 0.003, 1, 0, 104, 0, 1], [1, 0.006, 1, 0, 105, 0, 1], [1, 0.009, 1, 0, 106, 0, 1],
           [1, 0, 0.7, 0, 107, 0, 1], [1, 0, 1.25, 0, 108, 0, 1],
           [1, 0, 1, 0.06, 109, 0, 1], [1, 0, 1, -0.06, 110, 0, 1],
           [1, 0, 1, 0, 111, 0.75, 1], [1, 0, 1, 0, 112, 0.43, 1],
           [2, 0.008, 0.85, 0.04, 113, 0.75, 0.85], [3, 0.010, 1.15, -0.05, 114, 0.6, 1.15],
           [1, 0, 0.55, 0, 115, 0, 0.55], [1, 0, 1.45, 0, 116, 0, 1.45],
           [1, 0, 0.55, -0.05, 117, 0.75, 0.55], [2, 0.009, 0.75, 0.03, 118, 0.6, 0.75]];
  M.forEach(function (m) { add(modeRob(OVR, m[0], m[1], m[2], m[3], m[4], m[5], out, m[6])); });
  out.push('');
  out.push('═══ 5/6 过渡期串词（决定 STATIC_HOLD_MS 下限）═══');
  add(modeRuns(OVR, out));
  out.push('');
  out.push('═══ 6/6 词库外录制不应出图（防"做别的手势蹦出旧词"）═══');
  add(modeForeign(OVR, out));
  out.push('');
  out.push('════════ 汇总 ' + sum.ok + '/' + sum.n + (sum.ok === sum.n ? ' 全部通过 ✅' : ' 有 ' + (sum.n - sum.ok) + ' 项失败 ❌') + ' ════════');
}

function run(argv) {
  var mode = String(argv[0] || 'all');
  var OVR = {};
  try { OVR = JSON.parse(String(argv[1] || '{}')); } catch (e) { OVR = {}; }
  var out = [];
  if (mode === 'all') { modeAll(OVR, out); }
  else if (mode === 'words') { modeWords(out); }
  else if (mode === 'single') { modeSingle(OVR, parseInt(String(argv[2] || '30'), 10), out); }
  else if (mode === 'seq') { modeSeq(OVR, parseInt(String(argv[2] || '30'), 10), String(argv[3] || ''), out); }
  else if (mode === 'rob') {
    modeRob(OVR, parseInt(String(argv[3] || '1'), 10), parseFloat(String(argv[4] || '0')),
      parseFloat(String(argv[5] || '1')), parseFloat(String(argv[6] || '0')),
      parseInt(String(argv[7] || '1'), 10), parseFloat(String(argv[8] || '0')), out,
      parseFloat(String(argv[9] || '1')));
  }
  else if (mode === 'morph') {
    modeMorph(OVR, String(argv[3] || ''), String(argv[4] || '1,1,1,1,1').split(',').map(parseFloat),
      String(argv[5]) === 'dbg', out);
  }
  else if (mode === 'feat') { modeFeat(OVR, String(argv[3] || ''), out); }
  else if (mode === 'runs') { modeRuns(OVR, out); }
  else if (mode === 'foreign') { modeForeign(OVR, out); }
  else if (mode === 'hold') { modeHold(OVR, out); }
  else if (mode === 'hud') { modeHud(String(argv[1] || ''), parseFloat(String(argv[2] || '2')), out); }
  else { out.push('未知模式: ' + mode + '（可用: all words single seq rob morph feat runs hold foreign hud）'); }
  return out.join('\n');
}
