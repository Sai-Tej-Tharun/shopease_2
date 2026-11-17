"""Simultaneous stock-update test against a RUNNING service (mandatory scenario: concurrent updates).

Creates a product with 20 units, fires 30 stock-out requests of 1 unit at the same moment (threads released
together by a barrier) and then checks the result through the API:
  * exactly 20 requests succeed (201), exactly 10 are refused (409 insufficient stock)
  * final stock is 0 (never negative) and the history has 20 OUT rows whose previous/updated values form an unbroken chain
Then it repeats with a mixed run of 20 stock-in and 20 stock-out requests and checks that the final stock is unchanged.

Windows:   python scripts\\concurrent_stock_test.py
Uses JWT_SECRET_KEY from the environment or from the .env file in the project folder (an admin token is signed locally).
Options:   --base-url http://localhost:8003   --requests 30   --stock 20
"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import jwt


def load_secret() -> str:
    secret = os.environ.get("JWT_SECRET_KEY")
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not secret and env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("JWT_SECRET_KEY="):
                secret = line.split("=", 1)[1].strip()
    if not secret:
        sys.exit("JWT_SECRET_KEY not found (set it in the environment or in .env)")
    return secret


def fire(n, fn):
    barrier, results = threading.Barrier(n), [None] * n

    def worker(i):
        barrier.wait()
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    started = time.perf_counter()
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results, time.perf_counter() - started


def check(label, condition, results):
    results.append((label, condition))
    print(("PASS  " if condition else "FAIL  ") + label)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8003")
    ap.add_argument("--requests", type=int, default=30)
    ap.add_argument("--stock", type=int, default=20)
    a = ap.parse_args()
    now = int(time.time())
    token = jwt.encode({"sub": "1", "role": "admin", "iat": now, "exp": now + 1800}, load_secret(), algorithm="HS256")
    h = {"Authorization": f"Bearer {token}"}
    http = httpx.Client(base_url=a.base_url, headers=h, timeout=60)
    run = str(int(time.time()))
    checks = []

    cat = http.post("/api/categories/", json={"name": f"Concurrency Test {run}"}).json()["id"]
    pid = http.post("/api/products/", json={"category_id": cat, "name": "Concurrency Item", "sku": f"CON-{run}", "price": "10.00",
                                            "stock_quantity": a.stock}).json()["id"]
    print(f"Product {pid} created with {a.stock} units. Sending {a.requests} simultaneous stock-out requests of 1 unit...")

    def out(i):
        r = httpx.post(f"{a.base_url}/api/inventory/{pid}/stock-out", json={"quantity": 1, "remarks": f"concurrent #{i}"}, headers=h, timeout=60)
        return r.status_code

    codes, secs = fire(a.requests, out)
    ok, refused = codes.count(201), codes.count(409)
    expected_ok = min(a.stock, a.requests)
    print(f"Responses: {ok} x 201, {refused} x 409, other: {[c for c in codes if c not in (201, 409)]}  ({secs:.2f}s)")
    stock = http.get(f"/api/inventory/{pid}").json()["stock_quantity"]
    rows = http.get(f"/api/inventory/{pid}/history", params={"page_size": 100}).json()["items"][::-1]  # oldest first
    outs = [r for r in rows if r["transaction_type"] == "OUT"]
    chain_ok = all(rows[i]["updated_stock"] == rows[i + 1]["previous_stock"] for i in range(len(rows) - 1))
    check(f"exactly {expected_ok} requests succeeded", ok == expected_ok, checks)
    check(f"the other {a.requests - expected_ok} were refused with 409", refused == a.requests - expected_ok, checks)
    check(f"final stock is {max(a.stock - a.requests, 0)} (never negative)", stock == max(a.stock - a.requests, 0), checks)
    check(f"history has {expected_ok} OUT rows (no lost or duplicated update)", len(outs) == expected_ok, checks)
    check("history chain is unbroken (each previous_stock = the row before's updated_stock)", chain_ok, checks)

    http.post(f"/api/inventory/{pid}/stock-in", json={"quantity": 100, "remarks": "refill for mixed test"})
    before = http.get(f"/api/inventory/{pid}").json()["stock_quantity"]
    n = 40

    def mixed(i):
        action = "stock-in" if i % 2 == 0 else "stock-out"
        return httpx.post(f"{a.base_url}/api/inventory/{pid}/{action}", json={"quantity": 3}, headers=h, timeout=60).status_code

    codes2, secs2 = fire(n, mixed)
    after = http.get(f"/api/inventory/{pid}").json()["stock_quantity"]
    rows2 = http.get(f"/api/inventory/{pid}/history", params={"page_size": 100}).json()
    rows2 = rows2["items"][::-1]
    print(f"Mixed run: {n} simultaneous requests (20 stock-in, 20 stock-out of 3 units): {codes2.count(201)} x 201  ({secs2:.2f}s)  stock {before} -> {after}")
    check("all 40 mixed requests succeeded", codes2.count(201) == n, checks)
    check("stock after the mixed run equals stock before (+60 - 60)", after == before, checks)
    check("mixed-run history chain is unbroken and ends at the real stock",
          all(rows2[i]["updated_stock"] == rows2[i + 1]["previous_stock"] for i in range(len(rows2) - 1)) and rows2[-1]["updated_stock"] == after, checks)

    http.delete(f"/api/products/{pid}")
    http.delete(f"/api/categories/{cat}")
    failed = [c for c, okc in checks if not okc]
    print(f"\n{len(checks) - len(failed)} of {len(checks)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
