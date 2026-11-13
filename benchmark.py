# benchmark.py
# 성능 비교: 같은 상품을 여러 번 요청할 때 캐시(Mini Redis)를 안 쓸 때 vs 쓸 때
# 실행: python benchmark.py   (서버를 따로 켤 필요 없음, 이 파일이 직접 띄움)

import json
import statistics
import threading
import time
import urllib.request

import guest_api
import server

REDIS_PORT, GUEST_PORT = 18100, 18101
N = 10                                    # 요청 횟수


def timed_get(url):
    start = time.perf_counter()
    with urllib.request.urlopen(url, timeout=10) as res:
        data = json.loads(res.read())
    return (time.perf_counter() - start) * 1000, data


def main():
    server.Handler.log_message = lambda *args: None
    redis_srv = server.make_server(REDIS_PORT)
    guest_srv = guest_api.make_server(GUEST_PORT)
    guest_api.REDIS_URL = f"http://localhost:{REDIS_PORT}"
    for s in (redis_srv, guest_srv):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    base = f"http://localhost:{GUEST_PORT}/products/1"
    print(f"상품 1번을 {N}번 요청 (DB 조회 1회 = {guest_api.SLOW_SECONDS}초 걸린다고 가정)\n")

    # 1) 캐시 없이
    no_cache = [timed_get(base + "?nocache=1")[0] for _ in range(N)]

    # 2) 캐시 사용 (첫 번째만 DB, 나머지는 캐시)
    with_cache, sources = [], []
    for _ in range(N):
        ms, data = timed_get(base)
        with_cache.append(ms)
        sources.append(data["source"])

    def row(name, xs):
        return f"{name:<22}{sum(xs):>10.1f}{statistics.mean(xs):>10.1f}{min(xs):>10.1f}{max(xs):>10.1f}"

    print(f"{'':<22}{'총(ms)':>10}{'평균':>10}{'최소':>10}{'최대':>10}")
    print(row("캐시 없음 (매번 DB)", no_cache))
    print(row("캐시 사용", with_cache))
    hit = [ms for ms, src in zip(with_cache, sources) if src == "cache"]
    print(row("  └ 캐시에서 꺼낸 것만", hit))
    print()
    print("캐시 사용 시 응답 출처:", " → ".join(sources))
    print(f"총 시간: {sum(no_cache):.0f}ms → {sum(with_cache):.0f}ms  "
          f"({sum(no_cache) / sum(with_cache):.1f}배 빠름)")
    print(f"캐시 적중 1회 평균: {statistics.mean(hit):.1f}ms  "
          f"(DB 1회 평균 {statistics.mean(no_cache):.1f}ms의 약 1/{statistics.mean(no_cache) / statistics.mean(hit):.0f})")

    redis_srv.shutdown()
    guest_srv.shutdown()


if __name__ == "__main__":
    main()
