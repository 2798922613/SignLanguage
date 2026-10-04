#!/usr/bin/env python3
"""
组词效果图片 → image-data.js 生成脚本

为什么需要它：
    index.html 通过 <script src="image-data.js"> 内嵌 base64 图片，
    这样 file:// 协议下 canvas.getImageData 不会被跨域污染（见 script.js 的 transformToImage）。
    因此每次替换或新增 组词效果/*.png 后，都必须重新运行本脚本刷新内嵌数据，
    否则页面显示的仍是旧图。

用法：
    cd SignLanguage
    python3 build_image_data.py

说明：
    - 统一缩放到最长边 MAX_SIDE 像素（保持宽高比、保留透明通道），以控制 image-data.js 体积；
    - 键按文件名的 Unicode 顺序排序，输出格式与旧版 image-data.js 保持一致。
"""

import base64
import io
import os

from PIL import Image

SRC_DIR = '组词效果'
OUT_FILE = 'image-data.js'
MAX_SIDE = 256


def encode_one(path):
    img = Image.open(path).convert('RGBA')
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def main():
    names = sorted(f[:-4] for f in os.listdir(SRC_DIR) if f.lower().endswith('.png'))
    lines = ['// Auto-generated image data - DO NOT EDIT', 'const IMAGE_DATA = {']
    for name in names:
        b64 = encode_one(os.path.join(SRC_DIR, name + '.png'))
        lines.append('  "%s": "data:image/png;base64,%s",' % (name, b64))
    lines.append('};')
    with open(OUT_FILE, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('已写入 %s，共 %d 张图片：' % (OUT_FILE, len(names)))
    for name in names:
        print('  -', name)


if __name__ == '__main__':
    main()
