import json
from http.server import BaseHTTPRequestHandler
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from terabox_browseruse import get_terabox_dlink


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/health' or path == '/api/health':
            self._json({'status': 'ok'})
            return
        self._json({'error': 'not found'}, 404)

    def do_POST(self):
        path = self.path.split('?')[0]
        if path in ('/api/terabox/direct', '/api/terabox/direct/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
            except Exception:
                self._json({'error': 'invalid json'}, 400)
                return
            url = req.get('url')
            fs_id = req.get('fs_id')
            if not url:
                self._json({'error': 'url required'}, 400)
                return
            try:
                res = asyncio.run(get_terabox_dlink(url, fs_id))
            except Exception as e:
                self._json({'error': f'failed: {e}'}, 500)
                return
            if not res.get('success'):
                self._json({'error': res.get('error')}, 400)
                return
            self._json({'dlink': res['dlink'], 'ok': True})
            return
        self._json({'error': 'not found'}, 404)

    def log_message(self, fmt, *args):
        pass


app = Handler  # for some runtimes
