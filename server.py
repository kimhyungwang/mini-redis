# server.py
# 외부에서 Mini Redis를 쓸 수 있게 하는 HTTP API 서버 (파이썬 기본 라이브러리만 사용)
#
#   PUT    /keys/<key>   body: {"value": "철수", "ttl": 10}   → 저장 (ttl은 생략 가능)
#   GET    /keys/<key>                                     → 조회
#   DELETE /keys/<key>                                     → 삭제
#   POST   /incr/<key>   body: {"amount": 1} (생략 가능)     → 숫자 올리기 (원자적)
#   GET    /debug                                          → 해시 테이블 내부 모습 (화면용)
#   GET    /                                               → 화면 (index.html)
#
# 실행: python server.py  →  브라우저에서 http://localhost:8000

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from mini_redis import MiniRedis

store = MiniRedis()   # 서버 전체가 함께 쓰는 저장소 하나
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):
    # ── 응답 도우미 ──
    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # "/keys/name" → "name" (한글 키는 주소에서 %EC... 로 오기 때문에 unquote로 되돌림)
    def key_from_path(self):
        if not self.path.startswith("/keys/"):
            return None
        return unquote(self.path[len("/keys/"):])

    # ── 조회 ──
    def do_GET(self):
        if self.path == "/":
            with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/debug":
            return self.send_json(200, store.snapshot())

        if not self.path.startswith("/keys/"):
            return self.send_json(404, {"error": "없는 주소예요"})
        key = self.key_from_path()
        if not key:
            return self.send_json(400, {"error": "키가 비어 있어요"})

        value = store.get(key)
        if value is None:
            return self.send_json(404, {"error": "키가 없거나 만료됐어요", "key": key})
        return self.send_json(200, {"key": key, "value": value, "ttl": store.ttl(key)})

    # ── 저장 ──
    def do_PUT(self):
        if not self.path.startswith("/keys/"):
            return self.send_json(404, {"error": "없는 주소예요"})
        key = self.key_from_path()
        if not key:
            return self.send_json(400, {"error": "키가 비어 있어요"})

        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "JSON 형식이 아니에요"})

        if not isinstance(data, dict) or "value" not in data:
            return self.send_json(400, {"error": "value가 필요해요"})
        # [추가] null은 "키 없음"과 구분이 안 돼서 저장하지 않음 (실제 Redis도 null 값은 없음)
        if data["value"] is None:
            return self.send_json(400, {"error": "value는 null일 수 없어요"})

        ttl = data.get("ttl")
        if ttl is not None:
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
                return self.send_json(400, {"error": "ttl은 0보다 큰 숫자(초)여야 해요"})

        store.set(key, data["value"], ttl)
        return self.send_json(200, {"key": key, "value": data["value"], "ttl": store.ttl(key)})

    # ── 숫자 올리기 (INCR) ──
    def do_POST(self):
        if not self.path.startswith("/incr/"):
            return self.send_json(404, {"error": "없는 주소예요"})
        key = unquote(self.path[len("/incr/"):])
        if not key:
            return self.send_json(400, {"error": "키가 비어 있어요"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "JSON 형식이 아니에요"})
        amount = data.get("amount", 1) if isinstance(data, dict) else None
        if isinstance(amount, bool) or not isinstance(amount, int):
            return self.send_json(400, {"error": "amount는 정수여야 해요"})
        try:
            value = store.incr(key, amount)
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        return self.send_json(200, {"key": key, "value": value})

    # ── 삭제 ──
    def do_DELETE(self):
        if not self.path.startswith("/keys/"):
            return self.send_json(404, {"error": "없는 주소예요"})
        key = self.key_from_path()
        if not key:
            return self.send_json(400, {"error": "키가 비어 있어요"})
        if store.delete(key):
            return self.send_json(200, {"deleted": key})
        return self.send_json(404, {"error": "키가 없거나 만료됐어요", "key": key})

    # 화면이 1초마다 /debug를 부르는데, 그 로그가 터미널을 덮지 않게 끔
    def log_message(self, format, *args):
        if "/debug" not in self.path:
            super().log_message(format, *args)


# 서버 만들기 (테스트에서 다른 포트로 띄울 때도 사용)
# ThreadingHTTPServer: 요청마다 스레드를 따로 만들어 동시에 처리 → 그래서 MiniRedis에 락이 필요
class Server(ThreadingHTTPServer):
    # [추가] 동시에 들어와 기다릴 수 있는 연결 수 (기본 5 → 128)
    # 기본값이면 요청이 한꺼번에 100개 오면 일부 연결이 끊김(Connection reset)
    request_queue_size = 128
    daemon_threads = True


def make_server(port=8000):
    return Server(("localhost", port), Handler)


if __name__ == "__main__":
    # 만료된 키를 백그라운드에서 자동으로 지움 (지운 키는 터미널에 표시)
    store.start_active_expire(on_removed=lambda keys: print(f"[자동 만료 삭제] {', '.join(keys)}"))
    server = make_server(8000)
    print("Mini Redis 실행 중 → http://localhost:8000  (종료: Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
