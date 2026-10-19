#!/usr/bin/env python3
"""
本地开发服务器（禁用缓存版）

为什么需要它：
    python3 -m http.server 不发送 Cache-Control，Chrome 会按启发式规则缓存
    image-data.js / 自建-templates.js / gesture-recognizer.js 等文件。
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
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


DIAG_LOG = '/tmp/sign_diag.log'

# MediaPipe 的本地依赖（vendor/mediapipe/）里有 .wasm / .data / .tflite / .binarypb。
# Python 3.9 的 SimpleHTTPRequestHandler 不认识这些后缀，会按 text/plain 发出去；
# 而 .wasm 的 Content-Type 不是 application/wasm 时，WebAssembly.instantiateStreaming
# 会直接失败 —— 症状同样是"页面开了、摄像头也亮了，但一根骨骼都不画"。
mimetypes.add_type('application/wasm', '.wasm')
for _ext in ('.data', '.tflite', '.binarypb', '.pb'):
    mimetypes.add_type('application/octet-stream', _ext)


class NoCacheHandler(SimpleHTTPRequestHandler):
    """除 vendor/ 外所有响应都禁用缓存，保证每次刷新拿到的都是最新文件。"""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.wasm': 'application/wasm',
        '.data': 'application/octet-stream',
        '.tflite': 'application/octet-stream',
        '.binarypb': 'application/octet-stream',
        '.js': 'text/javascript',
        '.json': 'application/json',
    }

    def do_POST(self):
        """静默遥测：页面把分段/匹配事件 POST 到 /diag，便于离线排查识别问题。"""
        if self.path != '/diag':
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(n).decode('utf-8', 'replace') if n else ''
            with open(DIAG_LOG, 'a', encoding='utf-8') as fh:
                fh.write(body + '\n')
        except Exception:
            pass
        self.send_response(204)
        self.end_headers()

    def end_headers(self):
        path = self.path.split('?', 1)[0]
        if path.startswith('/vendor/'):
            # vendor/ 里的文件按「包名@版本」存放，内容永不改变，可以长期缓存：
            # 光 pose 一个包就 50MB，禁缓存会让每次刷新都从磁盘重传一遍，
            # 首次出骨骼的时间又被拖长。
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        else:
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
