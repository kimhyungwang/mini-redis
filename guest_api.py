# guest_api.py
# Mini Redis를 "써 보는 쪽" = 손님 API (상품 정보 API)
#
# 상품 정보는 원래 DB에서 가져온다고 가정하고, 일부러 느리게(SLOW_SECONDS) 만듦
# Mini Redis를 캐시로 써서 두 번째 요청부터는 빠르게 응답 (Cache-Aside 패턴)
#
#   GET /products/<id>             → 캐시 확인 → 없으면 DB(느림) → 캐시에 저장 → 응답
#   GET /products/<id>?nocache=1   → 캐시 안 쓰고 항상 DB (성능 비교용)
#   PUT /products/<id>  {"price": 1500}
#                                  → DB 값을 바꾸고 캐시를 지움 (무효화: 옛날 값이 응답되지 않게)
#
# 실행 순서: python server.py (Mini Redis, 8000번) → python guest_api.py (손님 API, 8001번)

import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse, parse_qs

REDIS_URL = "http://localhost:8000"   # Mini Redis 주소
SLOW_SECONDS = 1.0                    # DB 조회에 걸리는 시간 흉내
CACHE_TTL = 30                        # 캐시 유지 시간(초)

# 가짜 DB
PRODUCTS = {
    "1": {"id": "1", "name": "키보드", "price": 50000},
    "2": {"id": "2", "name": "마우스", "price": 30000},
    "3": {"id": "3", "name": "모니터", "price": 200000},
}


# ── Mini Redis에 HTTP로 요청하는 작은 클라이언트 ──
def redis_request(method, key, body=None):
    url = f"{REDIS_URL}/keys/{quote(key, safe='')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def cache_get(key):
    status, data = redis_request("GET", key)
    return data["value"] if status == 200 else None


def cache_set(key, value, ttl):
    redis_request("PUT", key, {"value": value, "ttl": ttl})


def cache_delete(key):
    redis_request("DELETE", key)


# ── 느린 DB 조회 흉내 ──
def db_get_product(pid):
    time.sleep(SLOW_SECONDS)
    product = PRODUCTS.get(pid)
    return dict(product) if product else None


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def product_id(self):
        path = urlparse(self.path).path
        if not path.startswith("/products/"):
            return None
        return path[len("/products/"):]

    def do_GET(self):
        pid = self.product_id()
        if not pid:
            return self.send_json(404, {"error": "없는 주소예요"})
        nocache = parse_qs(urlparse(self.path).query).get("nocache") == ["1"]
        start = time.time()
        key = f"product:{pid}"

        # 1. 캐시 확인 (nocache면 건너뜀)
        product = None if nocache else cache_get(key)
        if product is not None:
            source = "cache"
        else:
            # 2. 없으면 DB에서 (느림)
            product = db_get_product(pid)
            if product is None:
                return self.send_json(404, {"error": "없는 상품이에요"})
            source = "db"
            # 3. 캐시에 저장 (nocache면 저장도 안 함)
            if not nocache:
                cache_set(key, product, CACHE_TTL)

        elapsed_ms = round((time.time() - start) * 1000, 1)
        return self.send_json(200, {"product": product, "source": source, "elapsed_ms": elapsed_ms})

    # 상품 가격 변경 → 캐시 무효화
    def do_PUT(self):
        pid = self.product_id()
        if not pid or pid not in PRODUCTS:
            return self.send_json(404, {"error": "없는 상품이에요"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            price = data["price"]
        except (ValueError, KeyError, TypeError):
            return self.send_json(400, {"error": "price가 필요해요"})
        PRODUCTS[pid]["price"] = price
        cache_delete(f"product:{pid}")       # 옛날 값이 캐시에 남아 있으면 안 되니까 지움
        return self.send_json(200, {"updated": PRODUCTS[pid]})

    def log_message(self, format, *args):
        pass   # 터미널 로그 끔


def make_server(port=8001):
    return ThreadingHTTPServer(("localhost", port), Handler)


if __name__ == "__main__":
    server = make_server(8001)
    print("손님 API 실행 중 → http://localhost:8001/products/1  (Mini Redis가 8000번에 켜져 있어야 해요)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
