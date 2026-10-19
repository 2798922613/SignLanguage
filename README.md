# 手语识别交互系统

基于 MediaPipe 的实时手语识别系统，支持 49 个手语词汇和组句识别。通过摄像头实时捕捉手势动作，利用骨骼点进行匹配识别。

## 功能特性

- **实时识别**：通过摄像头实时检测并识别手语词汇
- **49 个词汇**：支持常用手语词汇识别（你好、谢谢、欢迎等）
- **组句识别**：支持多词组合成句子（今天天气好、认识你很高兴等）
- **骨骼可视化**：实时显示手部及身体骨骼关键点
- **粒子特效**：识别成功后展示图片和文字粒子动画效果

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/2798922613/SignLanguage.git
cd SignLanguage
```

### 2. 下载 MediaPipe 依赖

由于 MediaPipe 离线依赖包较大（约 74MB），需要单独下载：

```bash
bash tools/fetch_vendor.sh
```

> 如果不下载，页面会自动回退到 CDN 加载，但国内网络可能不稳定。

### 3. 启动本地服务器

Python 3 自带 HTTP 服务器即可运行：

```bash
python3 serve.py
```

然后访问 `http://localhost:8080` 打开交互页面。

### 4. 授权摄像头

首次访问时，浏览器会请求摄像头权限，点击允许即可开始使用。

## 项目结构

```
SignLanguage/
├── index.html              # 主交互页面
├── skeleton-recorder.html  # 骨骼录制页面（用于采集数据）
├── script.js               # 主逻辑脚本
├── style.css               # 样式文件
├── serve.py                # Python HTTP 服务器
├── dataset/                # 数据集目录
│   ├── video/              # 原始视频（.webm 被忽略）
│   │   └── *.json          # 骨骼 JSON 数据（49 个词）
│   └── README.md           # 数据集构建说明
├── tools/                  # 数据处理工具
│   ├── extract_skeleton.py # 视频 → 骨骼 JSON 提取
│   ├── build_dataset.py    # 构建训练数据集
│   ├── build_sentences.py  # 生成句子配置
│   └── fetch_vendor.sh     # 下载 MediaPipe 依赖
├── 组词效果/               # 词汇效果图片
└── 自建-templates.js       # 词汇识别模板
```

## 数据集构建

如果需要添加新的手语词汇，请按照 `dataset/README.md` 中的说明操作：

1. 将录制的视频放入 `dataset/video/` 目录
2. 提取骨骼数据：`python3 tools/extract_skeleton.py --overlay`
3. 构建数据集：`python3 tools/build_dataset.py`

## 支持的词汇

共 49 个手语词汇，包括：

- 日常用语：你好、谢谢、再见、欢迎、对不起
- 家庭成员：我、你、家、朋友
- 动作词汇：吃、喝、学习、工作、喜欢、想要
- 时间词汇：今天、明天、早上、晚上
- 地点词汇：厕所、公交车、哪里
- 组句示例：今天天气好、认识你很高兴、欢迎来我家

完整词汇列表见 `dataset/词汇编号表.txt`。

## 系统要求

- 现代浏览器（Chrome/Edge/Firefox 最新版本）
- 摄像头权限
- Python 3.6+（用于本地服务器和数据处理）

## 注意事项

1. **摄像头权限**：首次使用需要在浏览器中授权摄像头访问
2. **光线要求**：在光线充足的环境下识别效果更好
3. **手势规范**：请按照标准手语动作进行演示
4. **网络依赖**：如未下载 MediaPipe 离线包，需要网络连接

## 技术栈

- **前端**：HTML5 + CSS3 + JavaScript
- **骨骼检测**：MediaPipe Hands + Pose
- **后端工具**：Python 3 + OpenCV
- **数据处理**：NumPy

## 开源协议

本项目仅供学习参考使用。

---

如有问题或建议，欢迎提出 Issue 或 Pull Request。
