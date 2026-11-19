# hash_table.py
# 직접 만든 해시 테이블 (해시 함수: FNV-1a, 충돌 해결: 체이닝)
# 실제 Redis(dict.c) 방식: 꽉 차면 2배 테이블을 새로 만들고, 요청이 올 때마다 조금씩 옮김 (점진적 rehash)
#
# 평소 (테이블 1개)
#   ht[0] = [ [], [["age",..]], [], [["name",..],["a",..]] ]        ← 4칸
#
# 늘리는 중 (테이블 2개)
#   ht[0] = [ [], [], [], [["name",..],["a",..]] ]                   ← 아직 안 옮긴 칸이 남아 있음
#   ht[1] = [ [], [["age",..]], [], [], [], [], [], [] ]             ← 2배(8칸), 옮겨진 키가 들어감
#   rehash_idx = 3  → 다음에 옮길 ht[0]의 칸 번호
#
# 다 옮기면 ht[1]이 새 ht[0]이 되고 ht[1]은 비움

import random

INITIAL_CAPACITY = 4   # 실제 Redis도 4칸에서 시작
REHASH_STEP = 1        # 요청 한 번마다 옮길 칸 개수 (일부러 작게 해서 옮기는 과정이 보이게 함)


class HashTable:
    def __init__(self, capacity=INITIAL_CAPACITY):
        self.ht = [self._new_table(capacity), None]   # ht[0]: 지금 테이블, ht[1]: 늘리는 중일 때만 있는 새 테이블
        self.rehash_idx = -1                          # -1이면 늘리는 중이 아님
        self.size = 0                                 # 저장된 키 개수 (두 테이블 합)

    @staticmethod
    def _new_table(capacity):
        return [[] for _ in range(capacity)]

    # 키(문자열) → 큰 숫자 (FNV-1a 32비트)
    # [수정] djb2(h * 33 + 글자코드) → FNV-1a
    # 이유: djb2는 칸 수가 8, 16, 32일 때 33이 1처럼 작동해서 결과가 "글자코드 합"이 됨 → 키가 몰림
    #
    # 예: "ab"
    #   h = 2166136261
    #   'a'(97): h ^= 97 → h *= 16777619
    #   'b'(98): h ^= 98 → h *= 16777619
    #   마지막: h ^= h >> 16
    @staticmethod
    def _hash(key):
        h = 2166136261                               # FNV 시작값 (offset basis)
        for b in key.encode("utf-8"):                # 글자를 바이트로 (한글은 글자당 3바이트)
            h ^= b                                   # ① 바이트를 XOR로 섞고
            h = (h * 16777619) & 0xFFFFFFFF          # ② 큰 소수(FNV prime)를 곱해 퍼뜨림, 32비트로 자르기
        h ^= h >> 16                                 # ③ 잘 섞인 위쪽 비트를 아래로 접어 넣음
                                                     #    (% 칸 수는 아래쪽 비트만 쓰기 때문)
        return h

    # 키가 들어갈 칸 (테이블 크기로 나눈 나머지)
    def _bucket(self, table, key):
        return table[self._hash(key) % len(table)]

    def is_rehashing(self):
        return self.rehash_idx != -1

    @property
    def capacity(self):
        return len(self.ht[0])

    # ── 늘리기 ──

    # 키 수가 칸 수만큼 차면(로드 팩터 1) 2배 테이블을 만들고 옮기기 "시작"만 함
    def _expand_if_needed(self):
        if self.is_rehashing():
            return                                    # 이미 옮기는 중이면 또 늘리지 않음
        if self.size >= len(self.ht[0]):
            self.ht[1] = self._new_table(len(self.ht[0]) * 2)
            self.rehash_idx = 0

    # ht[0]의 칸을 n개만 ht[1]로 옮김 (빈 칸은 건너뜀)
    def _rehash_step(self, n=REHASH_STEP):
        if not self.is_rehashing():
            return
        old, new = self.ht
        while n > 0 and self.rehash_idx < len(old):
            bucket = old[self.rehash_idx]
            if bucket:                                # 빈 칸이 아니면 안의 키를 전부 새 테이블로
                for pair in bucket:
                    self._bucket(new, pair[0]).append(pair)
                old[self.rehash_idx] = []
                n -= 1
            self.rehash_idx += 1

        if self.rehash_idx >= len(old):               # 다 옮김 → 새 테이블로 교체
            self.ht = [new, None]
            self.rehash_idx = -1

    # 지금 있는 테이블들 (늘리는 중이면 2개, 아니면 1개)
    def _tables(self):
        return [t for t in self.ht if t is not None]

    # 키를 찾음 → (그 키가 있는 칸 리스트, 칸 안의 위치) / 없으면 (None, -1)
    def _find(self, key):
        for table in self._tables():                  # 늘리는 중이면 ht[0], ht[1] 둘 다 찾아봄
            bucket = self._bucket(table, key)
            for i, pair in enumerate(bucket):
                if pair[0] == key:
                    return bucket, i
        return None, -1

    # ── 기본 기능 ──

    # 저장 (이미 있는 키면 값만 바꿈)
    def set(self, key, value):
        self._rehash_step()                           # 요청이 올 때마다 조금씩 옮김
        bucket, i = self._find(key)
        if bucket is not None:
            bucket[i][1] = value
            return
        # 새 키: 늘리는 중이면 새 테이블(ht[1])에, 아니면 ht[0]에 넣음
        target = self.ht[1] if self.is_rehashing() else self.ht[0]
        self._bucket(target, key).append([key, value])
        self.size += 1
        self._expand_if_needed()

    # 조회 → (찾았는지, 값)
    def get(self, key):
        self._rehash_step()
        bucket, i = self._find(key)
        if bucket is None:
            return False, None
        return True, bucket[i][1]

    # 삭제 → 지웠으면 True
    def delete(self, key):
        self._rehash_step()
        bucket, i = self._find(key)
        if bucket is None:
            return False
        bucket.pop(i)
        self.size -= 1
        return True

    # 옮기기(rehash) 없이 조회만 → (찾았는지, 값)
    # 백그라운드 만료 정리가 화면의 "늘리는 중" 과정을 몰래 진행시키지 않도록 따로 둠
    def peek(self, key):
        bucket, i = self._find(key)
        if bucket is None:
            return False, None
        return True, bucket[i][1]

    # [추가] 무작위 키 하나 뽑기 (실제 Redis의 dictGetRandomKey와 같은 아이디어)
    # 무작위 칸을 골라서 비어 있으면 다시 고르고, 키가 있으면 그 칸 안에서 하나를 고름
    def random_key(self):
        if self.size == 0:
            return None
        tables = self._tables()
        while True:
            table = random.choice(tables)
            bucket = table[random.randrange(len(table))]
            if bucket:
                return random.choice(bucket)[0]

    # 모든 (키, 값) 목록
    def items(self):
        return [(k, v) for table in self._tables() for bucket in table for k, v in bucket]
