# mini_redis.py
# HashTable 위에 "만료 시간(TTL)"과 "락(동시성)"을 얹은 저장소
#
# 해시 테이블에 실제로 저장되는 값의 모양:
#   "name" → {"value": "철수", "expire_at": 1731400000.5}   (만료 시각, 없으면 None)
#
# 만료된 키를 지우는 방법 2가지 (실제 Redis와 같음)
#   1. Lazy 삭제   : 조회했을 때 만료됐으면 그때 지움 (get, ttl, delete)
#   2. Active 삭제 : 1초에 10번, 만료 시간이 있는 키 중 20개를 무작위로 뽑아 만료된 것을 지움
#                    25% 넘게 만료돼 있으면 한 번 더 (최대 25ms까지)

import threading
import time

ACTIVE_EXPIRE_HZ = 10            # 1초에 몇 번 정리할지 (Redis 기본 hz = 10)
ACTIVE_EXPIRE_SAMPLE = 20        # 한 번에 뽑아 볼 키 개수 (Redis와 같음)
ACTIVE_EXPIRE_REPEAT_RATIO = 0.25  # 이 비율보다 많이 만료돼 있으면 한 번 더
ACTIVE_EXPIRE_TIME_LIMIT = 0.025   # 한 번 정리에 쓸 최대 시간(초) = 25ms

from hash_table import HashTable


class MiniRedis:
    def __init__(self):
        self.table = HashTable()
        # 서버는 요청마다 스레드를 따로 띄움 → 여러 요청이 동시에 table을 만질 수 있음
        # 락을 잡은 요청 하나만 table을 만지게 해서 값이 꼬이지 않게 함
        self.lock = threading.Lock()
        # [추가] 만료 시간이 있는 키만 따로 모아 둔 해시 테이블 (실제 Redis의 expires 사전과 같음)
        #   "name" → 1731400000.5
        # Active 삭제 때 만료 시간이 없는 키까지 뒤지지 않으려고 따로 둠
        self.expires = HashTable()

    def _is_expired(self, entry):
        return entry["expire_at"] is not None and time.time() >= entry["expire_at"]

    # 저장. ttl(초)을 주면 그 시간 뒤 만료
    def set(self, key, value, ttl=None):
        expire_at = time.time() + ttl if ttl is not None else None
        with self.lock:
            self.table.set(key, {"value": value, "expire_at": expire_at})
            if expire_at is None:
                self.expires.delete(key)          # TTL 없이 덮어쓰면 만료 목록에서 빠짐
            else:
                self.expires.set(key, expire_at)

    # 조회. 없거나 만료됐으면 None
    def get(self, key):
        with self.lock:
            found, entry = self.table.get(key)
            if not found:
                return None
            # [Lazy 삭제] 조회했는데 만료 시각이 지났으면 지금 지우고 "없음"으로 응답
            if self._is_expired(entry):
                self._remove(key)
                return None
            return entry["value"]

    # 삭제. 지웠으면 True
    def delete(self, key):
        with self.lock:
            found, entry = self.table.get(key)
            if not found:
                return False
            self._remove(key)
            # 이미 만료된 키였다면 "원래 없던 키"로 봄
            return not self._is_expired(entry)

    # [추가] 숫자 1 올리기 (실제 Redis의 INCR)
    # "읽기 → 더하기 → 쓰기"를 락 하나 안에서 한 번에 처리 → 중간에 다른 요청이 끼어들 수 없음
    # 키가 없거나 만료됐으면 0에서 시작, 만료 시간(TTL)은 그대로 유지 (실제 Redis와 같음)
    # 값이 정수가 아니면 ValueError
    def incr(self, key, amount=1):
        with self.lock:
            found, entry = self.table.get(key)
            if not found or self._is_expired(entry):
                if found:
                    self._remove(key)
                current, expire_at = 0, None
            else:
                current, expire_at = entry["value"], entry["expire_at"]
                if isinstance(current, bool) or not isinstance(current, int):
                    raise ValueError("정수가 아닌 값은 올릴 수 없어요")
            new_value = current + amount
            self.table.set(key, {"value": new_value, "expire_at": expire_at})
            return new_value

    # 저장소와 만료 목록 양쪽에서 지움 (락을 잡은 상태에서만 호출)
    def _remove(self, key):
        self.table.delete(key)
        self.expires.delete(key)

    # ── Active 삭제 ──

    # 한 번의 정리: 무작위 20개 검사 → 25% 넘게 만료면 반복 (최대 25ms)
    # 반환값: 이번에 지운 키 목록
    def active_expire_cycle(self):
        removed = []
        start = time.time()
        with self.lock:
            while self.expires.size > 0:
                expired = 0
                samples = min(ACTIVE_EXPIRE_SAMPLE, self.expires.size)
                for _ in range(samples):
                    key = self.expires.random_key()
                    found, expire_at = self.expires.peek(key)
                    if found and time.time() >= expire_at:
                        self._remove(key)
                        removed.append(key)
                        expired += 1
                if expired / samples <= ACTIVE_EXPIRE_REPEAT_RATIO:
                    break                                  # 만료된 게 적으면 이번엔 끝
                if time.time() - start > ACTIVE_EXPIRE_TIME_LIMIT:
                    break                                  # 너무 오래 잡고 있지 않게
        return removed

    # 백그라운드 스레드에서 1초에 10번 active_expire_cycle 실행
    def start_active_expire(self, on_removed=None):
        def loop():
            while True:
                time.sleep(1 / ACTIVE_EXPIRE_HZ)
                removed = self.active_expire_cycle()
                if removed and on_removed:
                    on_removed(removed)
        threading.Thread(target=loop, daemon=True).start()   # daemon: 서버가 꺼지면 같이 꺼짐

    # 남은 시간(초). 실제 Redis와 같은 규칙: -2 = 키 없음, -1 = 만료 시간 없음
    def ttl(self, key):
        with self.lock:
            found, entry = self.table.get(key)
            if not found or self._is_expired(entry):
                return -2
            if entry["expire_at"] is None:
                return -1
            return round(entry["expire_at"] - time.time(), 1)

    # 화면에 그리기 위한 해시 테이블 내부 모습 (만료된 키도 지우지 않고 그대로 보여줌)
    # 늘리는 중이면 ht[0](옛 테이블)과 ht[1](새 테이블)을 둘 다 보여줌
    def snapshot(self):
        with self.lock:
            now = time.time()

            def draw(table):
                buckets = []
                for bucket in table:
                    cells = []
                    for key, entry in bucket:
                        ttl = None if entry["expire_at"] is None else round(entry["expire_at"] - now, 1)
                        cells.append({
                            "key": key,
                            "value": entry["value"],
                            "ttl": ttl,
                            "expired": self._is_expired(entry),
                        })
                    buckets.append(cells)
                return buckets

            ht0, ht1 = self.table.ht
            return {
                "capacity": self.table.capacity,
                "size": self.table.size,
                "load_factor": round(self.table.size / self.table.capacity, 2),
                "rehashing": self.table.is_rehashing(),
                "rehash_idx": self.table.rehash_idx,
                "ht0": draw(ht0),
                "ht1": draw(ht1) if ht1 is not None else None,
            }
