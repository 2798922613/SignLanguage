#!/usr/bin/env python3
"""
本地开发服务器（禁用缓存版）

为什么需要它：
    python3 -m http.server 不发送 Cache-Control，Chrome 会按启发式规则缓存
    image-data.js(2.8MB) / slr500-templates.js / gesture-recognizer.js 等文件。
    改完代码后用 http://localhost 访问仍可能跑到旧缓存，而直接双击 index.html
    走的是 file:// 协议、完全绕过 HTTP 缓存 —— 这正是"双击能用、终端起服务不行"
    最常见的原因之一。

用法：
    cd SignLanguage
    python3 serve.py            # 默认 8080 端口
    python3 serve.py 9000       # 指定端口
然后浏览器打开 http://localhost:8080
"""

import sys
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class NoCacheHandler(SimpleHTTPRequestHandler):
    """所有响应都禁用缓存，保证每次刷新拿到的都是最新文件。"""

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write('[serve] %s\n' % (fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    handler = functools.partial(NoCacheHandler, directory='.')
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    url = f'http://localhost:{port}/index.html'
    print(f'手语识别系统本地服务器已启动: {url}')
    print('按 Ctrl+C 停止')
    print('注意: 必须通过 localhost/127.0.0.1 或 https 访问，否则浏览器会拒绝开启摄像头')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
        server.server_close()


if __name__ == '__main__':
    main()
