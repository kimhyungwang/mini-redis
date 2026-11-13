# test_unit.py
# 단위 테스트: 서버 없이 HashTable, MiniRedis를 직접 불러서 확인
# 실행: python -m unittest test_unit -v

import threading
import time
import unittest

from hash_table import HashTable
from mini_redis import MiniRedis


class HashTableTest(unittest.TestCase):
    def setUp(self):
        self.t = HashTable()

    # ── 저장 / 조회 / 삭제 ──
    def test_set_and_get(self):
        self.t.set("name", "철수")
        self.assertEqual(self.t.get("name"), (True, "철수"))

    def test_get_missing_key(self):
        self.assertEqual(self.t.get("없는키"), (False, None))

    def test_delete(self):
        self.t.set("name", "철수")
        self.assertTrue(self.t.delete("name"))
        self.assertEqual(self.t.get("name"), (False, None))
        self.assertEqual(self.t.size, 0)

    def test_delete_missing_key(self):
        self.assertFalse(self.t.delete("없는키"))

    # ── 덮어쓰기 ──
    def test_overwrite_keeps_one_key(self):
        self.t.set("name", "철수")
        self.t.set("name", "영희")
        self.assertEqual(self.t.get("name"), (True, "영희"))
        self.assertEqual(self.t.size, 1)          # 키가 두 개가 되면 안 됨

    # ── 해시 함수 ──
    def test_hash_is_deterministic(self):
        self.assertEqual(HashTable._hash("name"), HashTable._hash("name"))

    def test_korean_and_special_keys(self):
        for key in ["이름", "user:1", "a b", "😀", "a/b"]:
            self.t.set(key, key)
        for key in ["이름", "user:1", "a b", "😀", "a/b"]:
            self.assertEqual(self.t.get(key), (True, key))

    # ── 충돌(체이닝) ──
    def test_collision_both_keys_found(self):
        # 4칸 기준으로 같은 칸에 들어가는 서로 다른 키 두 개를 찾아서 넣어 봄
        keys, seen = [], {}
        i = 0
        while len(keys) < 2:
            k = f"k{i}"
            idx = HashTable._hash(k) % 4
            if idx in seen:
                keys = [seen[idx], k]
            seen[idx] = k
            i += 1
        t = HashTable(capacity=4)
        t.set(keys[0], 1)
        t.set(keys[1], 2)
        self.assertEqual(t.get(keys[0]), (True, 1))
        self.assertEqual(t.get(keys[1]), (True, 2))

    # ── 칸 늘리기 (점진적 rehash) ──
    def test_expand_starts_when_full(self):
        for i in range(4):                         # 4칸에 4개 → 늘리기 시작
            self.t.set(f"k{i}", i)
        self.assertTrue(self.t.is_rehashing())
        self.assertEqual(len(self.t.ht[1]), 8)

    def test_keys_found_during_rehash(self):
        for i in range(4):
            self.t.set(f"k{i}", i)
        self.assertTrue(self.t.is_rehashing())
        # 옮기는 도중에 모든 키를 하나씩 조회 (조회할 때마다 한 칸씩 옮겨짐)
        for i in range(4):
            self.assertEqual(self.t.get(f"k{i}"), (True, i))

    def test_rehash_finishes_and_capacity_doubles(self):
        for i in range(4):
            self.t.set(f"k{i}", i)
        for _ in range(10):                        # 명령을 여러 번 보내 옮기기를 끝냄
            self.t.get("k0")
        self.assertFalse(self.t.is_rehashing())
        self.assertEqual(self.t.capacity, 8)

    def test_many_keys(self):
        n = 1000
        for i in range(n):
            self.t.set(f"key{i}", i)
        self.assertEqual(self.t.size, n)
        for i in range(n):
            self.assertEqual(self.t.get(f"key{i}"), (True, i))
        for i in range(0, n, 2):
            self.assertTrue(self.t.delete(f"key{i}"))
        self.assertEqual(self.t.size, n // 2)
        self.assertEqual(self.t.get("key0"), (False, None))
        self.assertEqual(self.t.get("key1"), (True, 1))


class MiniRedisTest(unittest.TestCase):
    def setUp(self):
        self.r = MiniRedis()

    # ── 저장 / 조회 / 삭제 ──
    def test_set_get_delete(self):
        self.r.set("name", "철수")
        self.assertEqual(self.r.get("name"), "철수")
        self.assertTrue(self.r.delete("name"))
        self.assertIsNone(self.r.get("name"))

    def test_get_missing(self):
        self.assertIsNone(self.r.get("없는키"))
        self.assertFalse(self.r.delete("없는키"))

    def test_values_of_various_types(self):
        self.r.set("num", 42)
        self.r.set("obj", {"a": 1})
        self.r.set("list", [1, 2])
        self.assertEqual(self.r.get("num"), 42)
        self.assertEqual(self.r.get("obj"), {"a": 1})
        self.assertEqual(self.r.get("list"), [1, 2])

    # ── TTL ──
    def test_ttl_values(self):
        self.r.set("forever", 1)
        self.r.set("temp", 1, ttl=10)
        self.assertEqual(self.r.ttl("forever"), -1)   # 만료 없음
        self.assertEqual(self.r.ttl("없는키"), -2)     # 키 없음
        self.assertTrue(0 < self.r.ttl("temp") <= 10)

    def test_expired_key_not_returned(self):
        self.r.set("temp", "값", ttl=0.05)
        self.assertEqual(self.r.get("temp"), "값")
        time.sleep(0.1)
        self.assertIsNone(self.r.get("temp"))           # Lazy 삭제
        self.assertEqual(self.r.table.size, 0)          # 실제로 지워졌는지

    def test_overwrite_without_ttl_removes_ttl(self):
        self.r.set("k", 1, ttl=0.05)
        self.r.set("k", 2)                              # TTL 없이 덮어쓰기
        time.sleep(0.1)
        self.assertEqual(self.r.get("k"), 2)
        self.assertEqual(self.r.ttl("k"), -1)

    def test_overwrite_with_new_ttl(self):
        self.r.set("k", 1)
        self.r.set("k", 2, ttl=0.05)
        time.sleep(0.1)
        self.assertIsNone(self.r.get("k"))

    def test_delete_expired_key_returns_false(self):
        self.r.set("k", 1, ttl=0.05)
        time.sleep(0.1)
        self.assertFalse(self.r.delete("k"))            # 이미 만료 = 없던 키

    # ── Active 삭제 (조회 없이 자동 삭제) ──
    def test_active_expire_removes_without_get(self):
        for i in range(30):
            self.r.set(f"t{i}", i, ttl=0.05)
        for i in range(5):
            self.r.set(f"p{i}", i)
        time.sleep(0.1)
        while self.r.active_expire_cycle():             # 지울 게 없어질 때까지
            pass
        self.assertEqual(self.r.table.size, 5)          # 만료 없는 5개만 남음
        self.assertEqual(self.r.expires.size, 0)

    def test_active_expire_keeps_live_keys(self):
        self.r.set("live", 1, ttl=10)
        self.assertEqual(self.r.active_expire_cycle(), [])
        self.assertEqual(self.r.get("live"), 1)

    # ── 동시성 ──
    def test_concurrent_writes(self):
        # 스레드 10개가 동시에 100개씩 저장 → 1000개가 하나도 빠짐없이 있어야 함
        def worker(n):
            for i in range(100):
                self.r.set(f"t{n}-{i}", i)
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(self.r.table.size, 1000)
        for n in range(10):
            for i in range(100):
                self.assertEqual(self.r.get(f"t{n}-{i}"), i)


    # ── INCR (원자적 +1) ──
    def test_incr_basic(self):
        self.assertEqual(self.r.incr("count"), 1)        # 없는 키는 0에서 시작
        self.assertEqual(self.r.incr("count"), 2)
        self.assertEqual(self.r.incr("count", 10), 12)
        self.assertEqual(self.r.get("count"), 12)

    def test_incr_non_integer_fails(self):
        self.r.set("name", "철수")
        with self.assertRaises(ValueError):
            self.r.incr("name")
        self.assertEqual(self.r.get("name"), "철수")     # 값은 그대로

    def test_incr_keeps_ttl(self):
        self.r.set("count", 1, ttl=0.3)
        self.r.incr("count")
        self.assertGreater(self.r.ttl("count"), 0)       # 만료 시간이 사라지지 않음
        time.sleep(0.4)
        self.assertIsNone(self.r.get("count"))

    def test_incr_after_expire_starts_from_zero(self):
        self.r.set("count", 100, ttl=0.05)
        time.sleep(0.1)
        self.assertEqual(self.r.incr("count"), 1)

    # ── 동시성: GET + SET 은 사라지고, INCR 은 정확함 ──
    def _run_threads(self, fn, n=20):
        threads = [threading.Thread(target=fn) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def test_get_then_set_loses_updates(self):
        self.r.set("count", 0)

        def get_then_set():
            v = self.r.get("count")      # ① 읽기
            time.sleep(0.01)             #    (그 사이 다른 스레드가 끼어들 틈)
            self.r.set("count", v + 1)   # ② 쓰기

        self._run_threads(get_then_set, 20)
        self.assertLess(self.r.get("count"), 20)          # 20이 안 됨 = 갱신 손실

    def test_incr_concurrent_is_exact(self):
        self.r.set("count", 0)

        def incr_many():
            for _ in range(50):
                self.r.incr("count")

        self._run_threads(incr_many, 20)
        self.assertEqual(self.r.get("count"), 1000)       # 20 × 50 = 1000 정확


if __name__ == "__main__":
    unittest.main(verbosity=2)
