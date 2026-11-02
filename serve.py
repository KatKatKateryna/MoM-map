#!/usr/bin/env python3
"""Local dev server with HTTP Range request support (required for PMTiles)."""
import argparse
import os
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler


class RangeHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        range_header = self.headers.get('Range')
        if range_header:
            path = self.translate_path(self.path)
            if os.path.isfile(path):
                self._serve_range(path, range_header)
                return
        super().do_GET()

    def _serve_range(self, path, range_header):
        size = os.path.getsize(path)
        m = re.match(r'bytes=(\d*)-(\d*)', range_header)
        start = int(m.group(1)) if m.group(1) else 0
        end   = int(m.group(2)) if m.group(2) else size - 1
        end   = min(end, size - 1)
        length = end - start + 1

        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()

        with open(path, 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # Revalidate every file, so edited js/css are never mixed with stale cached copies
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port', type=int, default=8080)
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f'Serving http://localhost:{args.port}/')
    print('Ctrl+C to stop')
    HTTPServer(('', args.port), RangeHandler).serve_forever()
