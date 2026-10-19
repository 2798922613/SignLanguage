# dataset/ —— 你只需要往 video/ 里丢 MP4

## 你要做的事（3 步）

1. **建文件夹/丢视频**：把录好的视频放进 `dataset/video/`，文件名就是词条标签。
   支持格式：`.mp4 / .mov / .m4v / .webm / .avi / .mkv`（**webm 不用转 mp4**，OpenCV/FFMPEG 直接读）。
2. **告诉我**（或自己跑）：`python3 tools/extract_skeleton.py --overlay`
3. **生成数据集**：`python3 tools/build_dataset.py`

完成后你会得到：

| 产物 | 说明 |
|---|---|
| `dataset/skeleton/今天_s01_001.json` | 每帧 21×2 手点 + 33 身体点，**这才是训练用的数据** |
| `dataset/preview/今天_s01_001.mp4` | 骨骼叠加预览，用来肉眼确认骨骼贴不贴合 |
| `自建-templates.js` | `index.html` 直接加载的多原型模板（10 关键帧 × 18 维）；SLR500 旧链路已删除 |
| `dataset/processed/dataset.npz` | 富特征 `X[N, 40, 146]`，给 DTW / 神经网络用 |
| `dataset/processed/labels.json` | 标签表、维度、每词条数、按说话人划分 |
| `dataset/processed/report.md` | 采集质量报告（哪些词样本不够、哪些检出率低） |

> **组句不在这条流水线里**：句子组合的唯一权威来源是 `dataset/词汇组句表.md`，
> 改完跑 `python3 tools/build_sentences.py` 生成根目录的 `sentences.js`（含词序拆解、词编号、图片名，并自动做前缀/后缀冲突校验）。
> 相关文档：`dataset/词汇编号表.txt`（40 词编号）、`dataset/词汇动作描述.md`（每个词怎么做）。

---

## 命名规则（两种任选其一）

**A. 平铺在 `video/` 下**

```
dataset/video/今天_s01_001.mp4     → 词=今天  说话人=s01  序号=001
dataset/video/今天_s01_002.mp4     → 词=今天  说话人=s01  序号=002
dataset/video/今天.mp4             → 词=今天  说话人=s00  序号=001
dataset/video/今天_003.mp4         → 词=今天  说话人=s00  序号=003
```

规则：`词_说话人_序号`，用下划线分隔；说话人形如 `s01`/`s02`（可省略，默认 `s00`）；
**第一段永远是词条标签**，所以词名里不要带下划线。

**B. 每词一个子文件夹**

```
dataset/video/今天/s01_001.mp4     → 词=今天（取文件夹名） 说话人=s01 序号=001
dataset/video/今天/s01_002.mp4
dataset/video/天气/s01_001.mp4     → 词=天气
```

先用 `python3 tools/extract_skeleton.py --list` 干跑一次，确认解析结果无误再正式提取。

---

## 录制要求（决定识别上限，务必照做）

- **机位固定**：正对胸口，支架/靠墙，全程不动；镜头高度约与胸齐。
- **取景**：人占画面高度 60%–80%；**双手在任何动作阶段都不能出框**（出框 = 该帧无手部骨骼）。
- **原始画面即可**：不需要事先叠加骨骼，脚本会自己提取；也不要加字幕/水印/画中画。
- **时长**：每条 1–2 秒，动作前后各留 0.3 秒静止（脚本会自动裁掉静止段）。
- **每词条数**：起步 8–15 条；混入 快/慢、大幅/小幅、左手起手/右手起手。
- **换背景换衣服**：同一个词分 2–3 个时段录，防止模型记住背景。
- **说话人**：至少 2 人；划分测试集时按说话人划分（脚本自动做），随机划分会让指标虚高。
- **竖拍视频**：手机竖拍若方向不对，加 `--rotate 90`（或 `--rotate auto` 读元数据）。

---

## 常用命令

```bash
# 0. MediaPipe 离线依赖自检（页面没有骨骼/提示"身体骨骼没开启"时先跑这个）
bash tools/fetch_vendor.sh --check          # 不完整就执行：bash tools/fetch_vendor.sh

# 干跑：只看命名解析对不对
python3 tools/extract_skeleton.py --list

# 正式提取（含骨骼预览视频，第一次务必加 --overlay 检查骨骼准不准）
python3 tools/extract_skeleton.py --overlay

# 只重跑某个词 / 强制覆盖已有结果
python3 tools/extract_skeleton.py --only 今天 --force

# 竖拍视频 / 降低手部检测阈值
python3 tools/extract_skeleton.py --rotate 90 --min-hand-conf 0.4

# 生成数据集与模板
python3 tools/build_dataset.py
python3 tools/build_dataset.py --proto 5 --rich-length 48      # 更多原型 / 更长序列
python3 tools/build_dataset.py --test-speaker s02              # 指定测试集说话人

# 离线回放回归（不用摄像头、不用浏览器）：改了判据/阈值后必跑，期望最后一行是"全部通过 ✅"
osascript -l JavaScript tools/replay_test.js all
osascript -l JavaScript tools/replay_test.js words             # 录制文件清单 + 词库覆盖情况
osascript -l JavaScript tools/replay_test.js foreign           # 未进词库的手势不应出图（防串词）
osascript -l JavaScript tools/replay_test.js feat '{}' 30 你_s01_002   # 逐帧特征值，调阈值用
osascript -l JavaScript tools/replay_test.js feat '{}' 30 认识_s01_009 # 双手词会多打一行「‖双手 pDxProd=… pGapR=…」
osascript -l JavaScript tools/replay_test.js hold              # 每词"判据首次命中→出图"的最短保持时长
osascript -l JavaScript tools/replay_test.js hud 你_s01_002 2.0        # 看页面右下角 HUD 会显示什么
osascript -l JavaScript tools/replay_test.js rob '{}' 0 1 0 0.55 0 121 0 0.55   # 末位=z 同比缩放（真实远近）
```

提取完成后如果打印 `⚠ 手部检出率偏低`，先看 `dataset/preview/` 里的预览视频，
再按提示调整 `--rotate` / `--min-hand-conf` / 取景与光线。

`tools/replay_test.js` 把 `dataset/video/*.json` 按原始时间戳喂给真实的 `GestureRecognizer`，
`all` 模式一次跑完 6 类回归：单段回放（TAIL=0/30）、组句序列（含重复词与易混对相邻，词间 300/800/1500ms）、
扰动（18 组：丢帧到 20/10fps、landmark 噪声 σ≤0.012、远近缩放 0.55–1.45、机位 Δy±0.06、宽高比 0.43–1.00 及叠加）、
过渡期串词时长、以及**词库外录制不应出图**。新录制会自动被发现（按 `<词>_<说话人>_<编号>.json` 解析），
还没进 `STATIC_RULES`/`STATIC_RULES2`/`STATIC_WORDS`/`CUSTOM_WORDS` 的词会被标成 `⬜ 未进`，不参与打分但会做串词预警。
当前基线：**239/240**（2026-10-11，11 词：001-011）。唯一红项是 `foreign` 里还没写判据的 **015 家**
（双手搭"∧"形，掌面竖直 → 双手判据不中，回落到单手被 003 今天 接走），接 015 时按
`dataset/词汇动作描述.md` §2.2/§2.4 的说法给它一条双手判据即可，不要去收紧 003。

扰动模式的"缩放"有两种口径，由末位参数 `ZS` 控制 z 是否跟着 x/y 同比缩放：
`ZS=SCALE` 是**真实的"人离镜头远近"**（相似变换，比值特征本该完全不变，0.55×/1.45× 都全过）；
`ZS=1`（默认）只缩 x/y、z 不动，模拟 **z 标定偏差 / 深度噪声被相对放大**，比真实情况更狠。
任一组失败时会直接打印「最长连续命中 Xms + 被挡住最多的维度 + 实际值」，不用再手工排查。

阈值不要只按 `dataset/video/` 的录制标定 —— 每词只有 1 条录制、姿势比现场标准，会把阈值定得偏紧。
`serve.py` 会把识别页每帧的特征追加写进 `/tmp/sign_diag.log`，其中 `static-hit` 事件就是用户**真做出图**那一帧；
调完阈值后把新的现场范围补进 `dataset/词汇动作描述.md` §2.1 的最后一列，再重跑 `all` 确认全绿。

---

## 说明

- `dataset/video/`、`dataset/preview/` 已加入 `.gitignore`（视频体积大，不入库）；
  `dataset/skeleton/`、`dataset/processed/` 会入库（JSON/npz 体积小，且是真正的数据集）。
- 骨骼 JSON 格式 `signlanguage-skeleton-v1` 与 `skeleton-recorder.html` 浏览器内导出的完全一致，
  两种来源的数据可以直接混在 `dataset/skeleton/` 里一起构建。
- **页面按 `R` 导出的 `.json` 不要放进 `video/`**：它已经是数据集格式本身，直接丢进
  `dataset/skeleton/` 即可，**跳过提取步骤**，然后跑 `build_dataset.py`。
- 页面按 `V` 导出的 `.webm` 可以放进 `video/`（格式没问题），但注意它**烧进了骨骼线且经过浏览器编码**，
  手部细节损失大、二次提取检出率会很低——要当数据集源请录原始画面。
- 坐标约定：归一化 [0,1]，原点为原始帧左上角，x 向右、y 向下，z 为相对深度（负值更靠近摄像头）。

## 页面没有骨骼 / 提示"身体骨骼没开启"（排查顺序）

两个页面（识别页 `index.html`、录制页 `skeleton-recorder.html`）的 MediaPipe 依赖现在是
**本地优先、CDN 兜底**，加载器是 `mp-loader.js`：先取仓库内
`vendor/mediapipe/npm/@mediapipe/<包>@<版本>/`，缺文件才回退 jsdelivr / unpkg。
2026-10-09 之前两页写死从 `cdn.jsdelivr.net` 拉 `hands.js` / `pose.js` 及其 wasm+tflite
（pose 一个包 50MB），国内网络下会挂起十几秒甚至完全不通，Hands/Pose 永远初始化不了——
表现就是"摄像头亮了但一根骨骼都没有"，识别页一直显示
"静态判据：没拿到手+肩 → 请确认身体骨骼已开启"。

1. `bash tools/fetch_vendor.sh --check`：打印「vendor 依赖完整 ✅」才算好。不完整就跑
   `bash tools/fetch_vendor.sh`（约 74MB；国内可用
   `NPM_REGISTRY=https://registry.npmmirror.com bash tools/fetch_vendor.sh` 加速）。
2. 必须通过 `http://localhost:8080` 访问：直接双击 html 走 `file://`，浏览器禁止开摄像头。
   服务没起时双击 `打开手势交互页.command` / `打开骨骼录制页.command`，或 `python3 serve.py`。
3. 识别页右下角 HUD（按 `D` 显示）会写清是哪一种原因：`pose.js 没加载成功` /
   `已被自动停用（Pose 连续失败）` / `还没出人（首次加载模型要几秒）`。
   身体骨骼被自动停用后按 `P` 即可重试，不用刷新整页。
4. `vendor/` 不进 git（见 `.gitignore`），换电脑或误删后重跑第 1 步就能恢复。

## 镜像注意（重要）

`skeleton-recorder.html` 默认开启镜像预览（`M` 键切换），`V` 录下的 webm 是**镜像画面**；
而识别页 `index.html` 直接在未镜像的摄像头帧上检测。两者左右相反，直接提取会导致模板左右翻转、识别失败。
因此提取时必须加 `--mirror`：

```bash
python3 tools/extract_skeleton.py --mirror --force --fps 30
python3 tools/build_dataset.py
```

若录製时已按 `M` 关掉镜像，则提取不要加 `--mirror`。

## 识别协议（分段匹配）

> **001-011 例外（2026-10-06 起，006-010 于 2026-10-07、011 于 2026-10-11 接入）**：这 11 个词走「静态手形 + 位置判据」
> （`gesture-recognizer.js` 的 `STATIC_RULES` = 单手、`STATIC_RULES2` = 双手），不看运动轨迹 ——
> 做完动作**停住约 0.5 秒**即出图，DTW 对它们的命中一律否决（被否决的 DTW 命中会**回滚** `lastTriggerAt`，
> 否则一次没出图的 DTW 命中会占用 700ms 重复锁、把紧随其后的静态判据饿死）。
> 两只手同时检出时**先跑双手判据**（009 认识 / 011 高兴），命中即出词、不再看单手；没命中才回落到单手判据。
> 判据、阈值与离线实测见 `dataset/词汇动作描述.md` §二（§2.1 单手 / §2.4 双手）；下面是 012 起仍适用的分段匹配协议。

识别页按「静止 → 动作1 → 保持 → 动作2 → 保持」协议工作：
- 起势（帧间运动能量 > `SEG_ONSET`）开始记段；连续静止 `SEG_END_STILL` 帧（约0.6s）结束一段并匹配；
- 段内的"保持/静止"帧会被压缩掉，只保留运动帧，因此保持 0.5~1s 不影响关键帧；
- 若段结束后 `SEG_MERGE_MS`(1.1s) 内再次起势，自动并入上一段（动作1+动作2 合成一个词）；
- 参数在 `gesture-recognizer.js` 构造函数里（`SEG_ONSET/SEG_STILL/SEG_END_STILL/SEG_MAX/SEG_MERGE_MS`）。
做完整套动作后停住约 0.6 秒即出结果与图片。

## 正确采集流程（2026-10-05 起）

录制页按 **V** 现在会**同时**下载 `<词>_<时间>.webm` 和 `<词>_<时间>.json`（同名成对）。
- JSON 里的 landmark 由**与识别页同一套 JS MediaPipe、在未镜像的原始摄像头帧**上计算，是建模板的唯一正确数据源；
- webm 仅作留档/预览，不再用于提取（Python 重提取存在运行时域差，已弃用）；
- 把成对文件放入 `dataset/video/` 后运行 `python3 tools/build_dataset.py`，脚本会自动优先使用 JSON；
- 同一词有多个 JSON 即多原型，识别更稳；识别页对「大致位置/方向/角度」容差匹配，且同一手势可重复识别。
