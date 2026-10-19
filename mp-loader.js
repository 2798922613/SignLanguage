/**
 * MediaPipe 依赖加载器 —— 识别页 index.html 与录制页 skeleton-recorder.html 共用。
 *
 * 为什么需要它：两页原先都在运行时从 cdn.jsdelivr.net 拉 hands.js / pose.js 以及它们的
 * wasm + tflite 模型（pose 一个包就 50MB）。国内网络下 jsdelivr 经常挂起十几秒甚至完全
 * 不通，表现就是"页面开了、摄像头也亮了，但一根骨骼都不画"，识别页还会因为拿不到
 * Pose 结果而一直提示"请确认身体骨骼已开启"。
 *
 * 现在的做法：依赖已经下载到仓库内 vendor/mediapipe/npm/（由 tools/fetch_vendor.sh 生成，
 * 不进 git），按 本地 → jsdelivr → unpkg 的顺序尝试；本地源还会先探测模型/wasm 资产是否
 * 齐全，缺文件就自动换下一个源，绝不会"加载了 js 却取不到模型"卡死。
 *
 * 版本必须两页钉得完全一致：不同 MediaPipe 版本输出的 landmark 有系统性偏差，
 * 录制页采的模板在识别页会整体对不上（见 gesture-recognizer.js 顶部注释）。
 */
(function (global) {
  'use strict';

  var LOCAL_BASE = 'vendor/mediapipe/npm';
  var BASES = [LOCAL_BASE, 'https://cdn.jsdelivr.net/npm', 'https://unpkg.com'];

  // assets = 该包运行时真正会去 fetch 的文件。本地源少一个就整体判为不可用，
  // 因为 MediaPipe 内部取模型失败只会抛在 send() 里，排查起来非常隐蔽。
  var SPECS = [
    {
      pkg: '@mediapipe/hands', ver: '0.4.1675469240', file: 'hands.js', global: 'Hands',
      assets: ['hands.binarypb', 'hands_solution_packed_assets.data',
        'hands_solution_packed_assets_loader.js',
        'hands_solution_simd_wasm_bin.js', 'hands_solution_simd_wasm_bin.wasm',
        'hands_solution_wasm_bin.js', 'hands_solution_wasm_bin.wasm',
        'hand_landmark_full.tflite', 'hand_landmark_lite.tflite']
    },
    {
      pkg: '@mediapipe/drawing_utils', ver: '0.3.1675466124', file: 'drawing_utils.js',
      global: 'drawConnectors', assets: []
    },
    {
      pkg: '@mediapipe/camera_utils', ver: '0.3.1675466862', file: 'camera_utils.js',
      global: 'Camera', assets: []
    },
    {
      pkg: '@mediapipe/pose', ver: '0.5.1675469404', file: 'pose.js', global: 'Pose',
      assets: ['pose_web.binarypb', 'pose_solution_packed_assets.data',
        'pose_solution_packed_assets_loader.js',
        'pose_solution_simd_wasm_bin.js', 'pose_solution_simd_wasm_bin.wasm',
        'pose_solution_wasm_bin.js', 'pose_solution_wasm_bin.wasm',
        'pose_landmark_full.tflite', 'pose_landmark_lite.tflite']
    }
  ];

  var RESOLVED = {};   // pkg -> 实际用的 base，locateFile 必须与之一致

  function isLocal(base) { return !/^https?:/i.test(base); }

  function pkgUrl(base, spec, file) {
    return base + '/' + spec.pkg + '@' + spec.ver + '/' + (file || spec.file);
  }

  function loadScript(url) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = url;
      s.async = false;
      s.onload = function () { resolve(url); };
      s.onerror = function () {
        if (s.parentNode) s.parentNode.removeChild(s);
        reject(new Error('脚本加载失败: ' + url));
      };
      document.head.appendChild(s);
    });
  }

  function probe(url) {
    if (typeof fetch !== 'function') return Promise.resolve(true);
    return fetch(url, { method: 'HEAD', cache: 'no-store' })
      .then(function (r) { return !!(r && r.ok); })
      .catch(function () { return false; });
  }

  /** 本地源逐个探测资产；CDN 包是原子发布的，js 能下就说明资产在，不必白跑一圈 HEAD */
  async function assetsComplete(base, spec, log) {
    if (!isLocal(base)) return true;
    for (var i = 0; i < spec.assets.length; i++) {
      var ok = await probe(pkgUrl(base, spec, spec.assets[i]));
      if (!ok) {
        if (log) log('本地 vendor 缺 ' + spec.assets[i] + '（跑 bash tools/fetch_vendor.sh 补齐）', true);
        return false;
      }
    }
    return true;
  }

  /** 依次尝试各源，返回可用的 base；全部失败返回 null */
  async function loadPackage(spec, log) {
    for (var i = 0; i < BASES.length; i++) {
      var base = BASES[i];
      var where = isLocal(base) ? '本地 vendor' : base.replace(/^https?:\/\//, '');
      if (log) log('加载 ' + spec.file + ' ← ' + where);
      try {
        await loadScript(pkgUrl(base, spec));
      } catch (e) {
        if (log) log(spec.file + ' 在 ' + where + ' 取不到，换下一个源', true);
        continue;
      }
      if (typeof global[spec.global] === 'undefined') {
        if (log) log(spec.file + ' 已加载但未定义 ' + spec.global + '，换下一个源', true);
        continue;
      }
      if (!await assetsComplete(base, spec, log)) continue;
      RESOLVED[spec.pkg] = base;
      return base;
    }
    return null;
  }

  /** 按依赖顺序全部加载，返回失败的文件名数组（空数组 = 全好） */
  async function loadAll(log) {
    var failed = [];
    for (var i = 0; i < SPECS.length; i++) {
      var base = await loadPackage(SPECS[i], log);
      if (!base) failed.push(SPECS[i].file);
    }
    return failed;
  }

  /** 给 locateFile 用：某包实际解析到的 base，没解析到就退回传入的兜底值 */
  function baseUrl(pkg, fallback) {
    return RESOLVED[pkg] || fallback || BASES[1];
  }

  function specOf(pkg) {
    for (var i = 0; i < SPECS.length; i++) if (SPECS[i].pkg === pkg) return SPECS[i];
    return null;
  }

  global.MP_LOCAL_BASE = LOCAL_BASE;
  global.MP_BASES = BASES;
  global.MP_SPECS = SPECS;
  global.MP_RESOLVED = RESOLVED;
  global.mpLoadScript = loadScript;
  global.mpLoadPackage = loadPackage;
  global.mpLoadAll = loadAll;
  global.mpBaseUrl = baseUrl;
  global.mpSpecOf = specOf;
})(typeof window !== 'undefined' ? window : this);
