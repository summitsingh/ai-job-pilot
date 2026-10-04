#!/usr/bin/env python3
"""Minimal stdlib-only raw-websocket CDP driver. Runs ON browser host.

Usage: python3 amd_cdp.py <port> <cmd> [args...]
  reset                 close all page tabs, open fresh about:blank
  goto <url>            navigate first page tab
  info                  list page tabs [{url,title}]
  url                   current URL of first page tab
  text [maxchars]       visible text of first page tab
  shot <path>           screenshot to <path> (on the browser host)
  wait <secs>           sleep
  evalb64 <b64js>       Runtime.evaluate JS, print JSON result
  fclick <b64css>       scroll into view + real mouse click at element center
  ftype <b64css> <b64text>  CDP focus + trusted Input.insertText
  fkey <Enter|Escape|Tab|ArrowDown|ArrowUp>
  ffile <b64css> <path> DOM.setFileInputFiles (path on the browser host)

All output is a single JSON line: {"ok": true, ...} or {"ok": false, "error": ...}.
Selectors pass through base64 so digit-leading ids and quotes survive the shell.
"""
import sys
import json
import socket
import base64
import struct
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9226
HOST = "127.0.0.1"


def http_get(path):
    with urllib.request.urlopen(f"http://{HOST}:{PORT}{path}", timeout=20) as r:
        return r.read().decode()


def http_put(path):
    req = urllib.request.Request(f"http://{HOST}:{PORT}{path}", method="PUT")
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode()


def b64d(s):
    return base64.b64decode(s).decode()


class WS:
    def __init__(self, url):
        from urllib.parse import urlparse
        u = urlparse(url)
        host, port = u.hostname, u.port or 80
        self.sock = socket.create_connection((host, port), timeout=20)
        key = base64.b64encode(struct.pack("!d", time.time())).decode()
        req = (f"GET {u.path or '/'} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError("ws handshake failed")
        self._buf = b""
        self._id = 0

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("ws closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        mask = struct.pack("!I", 0x12345678)
        hdr = bytes([0x81])
        n = len(data)
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(hdr + mask + masked)

    def recv_msg(self, timeout=30):
        self.sock.settimeout(timeout)
        b1, b2 = self._read(2)
        fin, opcode = b1 >> 7, b1 & 0x0F
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack("!H", self._read(2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", self._read(8))[0]
        masked = (b2 >> 7) == 1
        mk = self._read(4) if masked else None
        payload = self._read(ln)
        if masked:
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise RuntimeError("ws close frame")
        return payload.decode()

    def call(self, method, params=None, timeout=30):
        self._id += 1
        mid = self._id
        self.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.recv_msg(timeout=max(1, deadline - time.time())))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
        raise RuntimeError("CDP timeout " + method)


def get_targets():
    return json.loads(http_get("/json/list"))


def page_ws():
    targets = [t for t in get_targets() if t.get("type") == "page"]
    if not targets:
        http_put("/json/new?about:blank")
        time.sleep(1)
        targets = [t for t in get_targets() if t.get("type") == "page"]
    return WS(targets[0]["webSocketDebuggerUrl"])


def emit(obj):
    print(json.dumps(obj, default=str))
    sys.stdout.flush()


def main():
    argv = sys.argv[2:]
    cmd = argv[0] if argv else "info"
    args = argv[1:]
    try:
        if cmd == "reset":
            # Open the fresh tab FIRST: headful Chrome exits cleanly (rc=0)
            # when its last window closes, which killed the Xvfb 9231 browser.
            # Headless is unaffected by the reorder.
            try:
                http_put("/json/new?about:blank")
            except Exception:
                pass
            time.sleep(1)
            keep = None
            for t in [x for x in get_targets() if x.get("type") == "page"]:
                if (t.get("url") or "").startswith("about:blank") and keep is None:
                    keep = t["id"]
                    continue
                try:
                    http_get("/json/close/" + t["id"])
                except Exception:
                    pass
            emit({"ok": True})
        elif cmd == "goto":
            ws = page_ws()
            ws.call("Page.navigate", {"url": b64d(args[0])})
            time.sleep(4)
            emit({"ok": True})
        elif cmd == "info":
            emit({"ok": True, "tabs": [
                {"url": t["url"], "title": t["title"]}
                for t in get_targets() if t.get("type") == "page"]})
        elif cmd == "url":
            ws = page_ws()
            r = ws.call("Runtime.evaluate",
                        {"expression": "location.href", "returnByValue": True})
            emit({"ok": True, "url": r.get("result", {}).get("value")})
        elif cmd == "text":
            n = int(args[0]) if args else 15000
            ws = page_ws()
            r = ws.call("Runtime.evaluate", {
                "expression": f"document.body?document.body.innerText.slice(0,{n}):''",
                "returnByValue": True})
            emit({"ok": True, "text": r.get("result", {}).get("value", "")})
        elif cmd == "shot":
            ws = page_ws()
            r = ws.call("Page.captureScreenshot", {"format": "png"})
            with open(args[0], "wb") as f:
                f.write(base64.b64decode(r["data"]))
            emit({"ok": True, "path": args[0]})
        elif cmd == "wait":
            time.sleep(float(args[0]))
            emit({"ok": True})
        elif cmd == "evalb64":
            ws = page_ws()
            r = ws.call("Runtime.evaluate", {
                "expression": b64d(args[0]),
                "returnByValue": True, "awaitPromise": True}, timeout=60)
            res = r.get("result", {})
            emit({"ok": True, "result": res.get("value", res)})
        elif cmd == "fclick":
            sel = b64d(args[0])
            ws = page_ws()
            ws.call("Runtime.evaluate", {"expression":
                f"(()=>{{const el=document.querySelector({json.dumps(sel)});"
                f"if(el){{el.scrollIntoView({{block:'center',behavior:'instant'}});return 1;}}return 0;}})()",
                "returnByValue": True})
            time.sleep(1.0)
            sid_ws = page_ws()
            doc = sid_ws.call("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
            node = sid_ws.call("DOM.querySelector",
                               {"nodeId": doc["nodeId"], "selector": sel})["nodeId"]
            if not node:
                emit({"ok": False, "error": "no such element: " + sel})
                return
            box = sid_ws.call("DOM.getBoxModel", {"nodeId": node})["model"]
            quad = box["content"]
            x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4
            y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4
            sid_ws.call("Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": x, "y": y})
            time.sleep(0.2)
            for t in ("mousePressed", "mouseReleased"):
                sid_ws.call("Input.dispatchMouseEvent", {
                    "type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
                time.sleep(0.15)
            emit({"ok": True, "x": x, "y": y})
        elif cmd == "fclickel":
            # b64 JS must return viewport {x,y} of the point to click
            # (used for react-select: click the container center, not the 4px input)
            ws = page_ws()
            r = ws.call("Runtime.evaluate", {
                "expression": b64d(args[0]), "returnByValue": True}, timeout=60)
            pt = r.get("result", {}).get("value", {}) or {}
            if "x" not in pt or not pt.get("inView", True):
                emit({"ok": False,
                      "error": "point not in view: " + str(pt)[:120]})
                return
            x, y = float(pt["x"]), float(pt["y"])
            time.sleep(0.6)
            ws.call("Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": x, "y": y})
            time.sleep(0.2)
            for t in ("mousePressed", "mouseReleased"):
                ws.call("Input.dispatchMouseEvent", {
                    "type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
                time.sleep(0.15)
            emit({"ok": True, "x": x, "y": y})
        elif cmd == "ftype":
            sel, text = b64d(args[0]), b64d(args[1])
            ws = page_ws()
            doc = ws.call("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
            node = ws.call("DOM.querySelector",
                           {"nodeId": doc["nodeId"], "selector": sel})["nodeId"]
            if not node:
                emit({"ok": False, "error": "no such element: " + sel})
                return
            ws.call("DOM.focus", {"nodeId": node})
            time.sleep(0.4)
            ws.call("Input.insertText", {"text": text})
            emit({"ok": True})
        elif cmd == "fkey":
            key = args[0]
            codes = {"Enter": (13, "Enter"), "Escape": (27, "Escape"),
                     "Tab": (9, "Tab"), "ArrowDown": (40, "ArrowDown"),
                     "ArrowUp": (38, "ArrowUp")}
            vk, k = codes[key]
            ws = page_ws()
            for t in ("keyDown", "keyUp"):
                p = {"type": t, "key": k, "code": k,
                     "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
                if t == "keyDown" and key == "Enter":
                    p["text"] = "\r"
                ws.call("Input.dispatchKeyEvent", p)
                time.sleep(0.15)
            emit({"ok": True})
        elif cmd == "ffile":
            sel, path = b64d(args[0]), args[1]
            ws = page_ws()
            doc = ws.call("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
            node = ws.call("DOM.querySelector",
                           {"nodeId": doc["nodeId"], "selector": sel})["nodeId"]
            if not node:
                emit({"ok": False, "error": "no such element: " + sel})
                return
            ws.call("DOM.setFileInputFiles", {"nodeId": node, "files": [path]})
            emit({"ok": True})
        else:
            emit({"ok": False, "error": "unknown cmd " + cmd})
    except Exception as e:
        emit({"ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
