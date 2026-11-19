# test_functional.py
# 기능 테스트: 실제로 서버(Mini Redis, 손님 API)를 띄우고 HTTP로 요청을 보내서 확인
# 실행: python -m unittest test_functional -v

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

import guest_api
import server
from mini_redis import MiniRedis

REDIS_PORT = 18000      # 테스트용 포트 (실제로 켜 둔 8000, 8001과 겹치지 않게)
GUEST_PORT = 18001


def request(method, url, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def redis(method, key, body=None, raw=None):
    return request(method, f"http://localhost:{REDIS_PORT}/keys/{quote(key, safe='')}", body, raw)


def guest(method, path, body=None):
    return request(method, f"http://localhost:{GUEST_PORT}{path}", body)


class FunctionalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.Handler.log_message = lambda *args: None   # 테스트 중 요청 로그 끄기
        # 두 서버를 백그라운드 스레드로 띄움
        cls.redis_server = server.make_server(REDIS_PORT)
        cls.guest_server = guest_api.make_server(GUEST_PORT)
        guest_api.REDIS_URL = f"http://localhost:{REDIS_PORT}"
        guest_api.SLOW_SECONDS = 0.3          # 테스트 시간을 줄이려고 0.3초로
        for s in (cls.redis_server, cls.guest_server):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.redis_server.shutdown()
        cls.guest_server.shutdown()

    def setUp(self):
        server.store = MiniRedis()            # 테스트마다 빈 저장소로 시작
        guest_api.CACHE_TTL = 30
        guest_api.PRODUCTS["1"]["price"] = 50000

    # ─────────── Mini Redis API: 정상 동작 ───────────
    def test_set_get_delete(self):
        self.assertEqual(redis("PUT", "name", {"value": "철수"})[0], 200)
        self.assertEqual(redis("GET", "name"), (200, {"key": "name", "value": "철수", "ttl": -1}))
        self.assertEqual(redis("DELETE", "name")[0], 200)
        self.assertEqual(redis("GET", "name")[0], 404)

    def test_overwrite(self):
        redis("PUT", "name", {"value": "철수"})
        redis("PUT", "name", {"value": "영희"})
        self.assertEqual(redis("GET", "name")[1]["value"], "영희")

    # ─────────── Mini Redis API: 엣지 케이스 ───────────
    def test_empty_key(self):
        self.assertEqual(redis("PUT", "", {"value": 1})[0], 400)
        self.assertEqual(redis("GET", "")[0], 400)

    def test_invalid_json(self):
        self.assertEqual(redis("PUT", "k", raw=b"{not json")[0], 400)

    def test_missing_value(self):
        self.assertEqual(redis("PUT", "k", {"ttl": 10})[0], 400)

    def test_null_value(self):
        self.assertEqual(redis("PUT", "k", {"value": None})[0], 400)

    def test_bad_ttl(self):
        for ttl in (-1, 0, "10", True):
            with self.subTest(ttl=ttl):
                self.assertEqual(redis("PUT", "k", {"value": 1, "ttl": ttl})[0], 400)

    def test_korean_and_special_keys(self):
        for key in ["이름", "user:1", "a b", "a/b", "😀"]:
            with self.subTest(key=key):
                self.assertEqual(redis("PUT", key, {"value": key})[0], 200)
                self.assertEqual(redis("GET", key)[1]["value"], key)

    def test_get_and_delete_missing(self):
        self.assertEqual(redis("GET", "없는키")[0], 404)
        self.assertEqual(redis("DELETE", "없는키")[0], 404)

    def test_get_right_after_expire(self):
        redis("PUT", "temp", {"value": 1, "ttl": 0.2})
        self.assertEqual(redis("GET", "temp")[0], 200)
        time.sleep(0.3)
        self.assertEqual(redis("GET", "temp")[0], 404)

    def test_large_value(self):
        big = "x" * 100_000
        self.assertEqual(redis("PUT", "big", {"value": big})[0], 200)
        self.assertEqual(len(redis("GET", "big")[1]["value"]), 100_000)

    def test_unknown_path(self):
        self.assertEqual(request("GET", f"http://localhost:{REDIS_PORT}/nope")[0], 404)

    def test_concurrent_requests(self):
        # 20개 요청을 동시에 보내도 하나도 빠지지 않아야 함
        threads = [threading.Thread(target=redis, args=("PUT", f"c{i}", {"value": i})) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for i in range(20):
            self.assertEqual(redis("GET", f"c{i}")[1]["value"], i)

    # ─────────── INCR ───────────
    def incr(self, key, body=None, raw=None):
        return request("POST", f"http://localhost:{REDIS_PORT}/incr/{quote(key, safe='')}", body, raw)

    def test_incr_http(self):
        self.assertEqual(self.incr("count"), (200, {"key": "count", "value": 1}))
        self.assertEqual(self.incr("count", {"amount": 5})[1]["value"], 6)

    def test_incr_edge_cases(self):
        redis("PUT", "name", {"value": "철수"})
        self.assertEqual(self.incr("name")[0], 400)                       # 숫자가 아닌 값
        self.assertEqual(self.incr("count", {"amount": "1"})[0], 400)     # amount가 문자열
        self.assertEqual(self.incr("count", {"amount": True})[0], 400)    # amount가 true
        self.assertEqual(self.incr("count", raw=b"{bad")[0], 400)         # 잘못된 JSON
        self.assertEqual(self.incr("")[0], 400)                           # 빈 키

    def test_incr_concurrent_http(self):
        threads = [threading.Thread(target=self.incr, args=("hits",)) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(redis("GET", "hits")[1]["value"], 50)

    # ─────────── 손님 API에서 캐시로 활용 ───────────
    def test_first_db_then_cache(self):
        s1, r1 = guest("GET", "/products/1")
        s2, r2 = guest("GET", "/products/1")
        self.assertEqual((s1, r1["source"]), (200, "db"))
        self.assertEqual((s2, r2["source"]), (200, "cache"))
        self.assertEqual(r1["product"], r2["product"])        # 내용은 같아야 함
        self.assertLess(r2["elapsed_ms"], r1["elapsed_ms"])    # 캐시가 더 빨라야 함
        # Mini Redis에 실제로 저장됐는지
        self.assertEqual(redis("GET", "product:1")[1]["value"]["name"], "키보드")

    def test_nocache_always_db(self):
        guest("GET", "/products/1")
        self.assertEqual(guest("GET", "/products/1?nocache=1")[1]["source"], "db")

    def test_invalidate_on_update(self):
        guest("GET", "/products/1")                             # 캐시에 50000 저장
        guest("PUT", "/products/1", {"price": 45000})           # 가격 변경 → 캐시 삭제
        s, r = guest("GET", "/products/1")
        self.assertEqual(r["source"], "db")                     # 캐시가 지워졌으니 DB에서
        self.assertEqual(r["product"]["price"], 45000)          # 옛날 값(50000)이 아님

    def test_cache_expires(self):
        guest_api.CACHE_TTL = 0.2
        guest("GET", "/products/2")
        time.sleep(0.3)
        self.assertEqual(guest("GET", "/products/2")[1]["source"], "db")

    def test_missing_product(self):
        self.assertEqual(guest("GET", "/products/999")[0], 404)
        self.assertEqual(redis("GET", "product:999")[0], 404)   # 없는 상품은 캐시하지 않음


if __name__ == "__main__":
    unittest.main(verbosity=2)
