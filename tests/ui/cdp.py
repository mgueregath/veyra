"""Cliente mínimo del protocolo de depuración de Chrome (sin dependencias) para probar la app en un navegador real."""
import base64, json, os, socket, struct, subprocess, time, urllib.request


class Chrome:
    def __init__(self, url, port=9333, size=(1600, 900), prof='/tmp/cdp_profile'):
        self.prof = prof
        self.proc = subprocess.Popen(['google-chrome', '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={port}',
                                      f'--window-size={size[0]},{size[1]}', f'--user-data-dir={prof}', 'about:blank'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=1).read(); break
            except Exception:
                time.sleep(0.25)
        req = urllib.request.Request(f'http://127.0.0.1:{port}/json/new?{url}', method='PUT')
        tab = json.loads(urllib.request.urlopen(req).read())
        self.ws_url = tab['webSocketDebuggerUrl']
        host, p = self.ws_url[5:].split('/')[0].split(':')
        self.s = socket.create_connection((host, int(p)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        path = '/' + self.ws_url[5:].split('/', 1)[1]
        self.s.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}:{p}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            buf += self.s.recv(4096)
        self.buf = buf.split(b'\r\n\r\n', 1)[1]
        self.i = 0
        self.call('Emulation.setDeviceMetricsOverride', width=size[0], height=size[1], deviceScaleFactor=1, mobile=False)

    def _send(self, text):
        data = text.encode(); n = len(data); mask = os.urandom(4)
        hdr = b'\x81' + (bytes([0x80 | n]) if n < 126 else b'\xfe' + struct.pack('>H', n) if n < 65536 else b'\xff' + struct.pack('>Q', n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[k % 4] for k, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise ConnectionError('Chrome cerró la conexión')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self):
        msg = b''
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7f
            if n == 126: n = struct.unpack('>H', self._read(2))[0]
            elif n == 127: n = struct.unpack('>Q', self._read(8))[0]
            msg += self._read(n)
            if b1 & 0x80:
                return msg.decode()

    def call(self, method, **params):
        self.i += 1; my = self.i
        self._send(json.dumps(dict(id=my, method=method, params=params)))
        while True:
            m = json.loads(self._recv())
            if m.get('id') == my:
                if 'error' in m: raise RuntimeError(m['error'])
                return m.get('result', {})

    def eval(self, expr):
        r = self.call('Runtime.evaluate', expression=expr, returnByValue=True, awaitPromise=True)
        if 'exceptionDetails' in r: raise RuntimeError(r['exceptionDetails'])
        return r['result'].get('value')

    def mouse(self, typ, x, y, button='left', buttons=1):
        self.call('Input.dispatchMouseEvent', type=typ, x=x, y=y, button=button if typ != 'mouseMoved' else 'none',
                  buttons=buttons if typ != 'mouseReleased' else 0, clickCount=1 if typ != 'mouseMoved' else 0)

    def drag(self, x0, y0, x1, y1, steps=8):
        self.mouse('mouseMoved', x0, y0); self.mouse('mousePressed', x0, y0)
        for k in range(1, steps + 1):
            self.mouse('mouseMoved', x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)
        self.mouse('mouseReleased', x1, y1)

    def key(self, k):
        self.call('Input.dispatchKeyEvent', type='keyDown', key=k, text=k if len(k) == 1 else '')
        self.call('Input.dispatchKeyEvent', type='keyUp', key=k)

    def close(self):
        try: self.call('Browser.close')
        except Exception: pass
        self.proc.terminate()
        try: self.proc.wait(timeout=5)
        except Exception: self.proc.kill()
