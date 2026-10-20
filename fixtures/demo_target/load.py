"""Load script: triggers both demo faults at recorded times and writes a run manifest (stdlib only).

    python load.py                         # against http://127.0.0.1:58080
    python load.py --fault1-seconds 30     # longer search storm

Writes runs/<run_id>/manifest.json with each phase's UTC window, request counts and sample request ids.
Those windows are what an investigation is started with, and what the golden-incident eval (F7) checks
against ground_truth/*.json. Exits 1 if a fault did not reproduce.
"""

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUSTOMERS = 50_000
SEEDED_ORDER_IDS = 100_000  # any id in this range exists with the default seed


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.statuses: Counter[int] = Counter()
        self.failed_request_ids: list[str] = []

    def record(self, status: int, request_id: str) -> None:
        with self.lock:
            self.statuses[status] += 1
            if status >= 500 and len(self.failed_request_ids) < 20:
                self.failed_request_ids.append(request_id)


def call(base_url: str, method: str, path: str, request_id: str, recorder: Recorder, timeout: float = 15) -> int:
    request = urllib.request.Request(base_url + path, method=method, headers={"X-Request-ID": request_id})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, TimeoutError):
        status = 599  # client-side timeout / connection failure
    recorder.record(status, request_id)
    return status


def phase(name: str, fn) -> dict:
    recorder = Recorder()
    started = utc_now()
    print(f"[{started}] {name} ...", flush=True)
    fn(recorder)
    ended = utc_now()
    summary = {
        "name": name,
        "start": started,
        "end": ended,
        "statuses": {str(k): v for k, v in sorted(recorder.statuses.items())},
        "failed_request_ids": recorder.failed_request_ids,
    }
    print(f"[{ended}] {name} done: {summary['statuses']}", flush=True)
    return summary


def normal_traffic(base_url: str, run_tag: str, seconds: float):
    def run(recorder: Recorder) -> None:
        deadline = time.monotonic() + seconds
        n = 0
        while time.monotonic() < deadline:
            n += 1
            call(base_url, "GET", f"/orders/{random.randint(1, SEEDED_ORDER_IDS)}", f"{run_tag}-n{n}", recorder)
            time.sleep(0.2)
    return run


def search_storm(base_url: str, run_tag: str, seconds: float, concurrency: int):
    def worker(recorder: Recorder, worker_id: int, deadline: float) -> None:
        n = 0
        while time.monotonic() < deadline:
            n += 1
            email = f"customer{random.randint(1, CUSTOMERS)}%40demo.invalid"
            call(base_url, "GET", f"/orders/search?email={email}", f"{run_tag}-s{worker_id}x{n}", recorder)

    def run(recorder: Recorder) -> None:
        deadline = time.monotonic() + seconds
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for worker_id in range(concurrency):
                pool.submit(worker, recorder, worker_id, deadline)
    return run


def refund_requests(base_url: str, run_tag: str, count: int):
    def run(recorder: Recorder) -> None:
        for n in range(1, count + 1):
            call(base_url, "POST", f"/refunds/{random.randint(1, SEEDED_ORDER_IDS)}", f"{run_tag}-r{n}", recorder)
            time.sleep(1)
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:58080")
    parser.add_argument("--baseline-seconds", type=float, default=10)
    parser.add_argument("--fault1-seconds", type=float, default=20)
    parser.add_argument("--fault1-concurrency", type=int, default=24)
    parser.add_argument("--fault2-requests", type=int, default=5)
    args = parser.parse_args()

    try:
        urllib.request.urlopen(args.base_url + "/health", timeout=5)
    except Exception as exc:
        print(f"demo-api is not reachable at {args.base_url}: {exc}\nStart it with: docker compose up -d --build")
        return 2

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = uuid.uuid4().hex[:4]
    phases = [
        phase("baseline", normal_traffic(args.base_url, tag, args.baseline_seconds)),
        phase("fault1_missing_index",
              search_storm(args.base_url, tag, args.fault1_seconds, args.fault1_concurrency)),
        phase("cooldown", normal_traffic(args.base_url, tag, 5)),
        phase("fault2_missing_config", refund_requests(args.base_url, tag, args.fault2_requests)),
    ]
    by_name = {p["name"]: p for p in phases}

    def server_errors(name: str, code: str) -> int:
        return by_name[name]["statuses"].get(code, 0)

    reproduced = {
        "fault1_missing_index": server_errors("fault1_missing_index", "503") > 0,
        "fault2_missing_config": server_errors("fault2_missing_config", "500") == args.fault2_requests,
    }
    manifest = {
        "run_id": run_id,
        "request_id_prefix": tag,
        "base_url": args.base_url,
        "phases": phases,
        "faults": {
            name: {
                "ground_truth": f"ground_truth/{name}.json",
                "window": {"start": by_name[name]["start"], "end": by_name[name]["end"]},
                "reproduced": reproduced[name],
            }
            for name in reproduced
        },
    }
    out = HERE / "runs" / run_id / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nManifest: {out}")

    for name, ok in reproduced.items():
        print(f"  {name}: {'reproduced' if ok else 'NOT reproduced'}")
    if not reproduced["fault1_missing_index"]:
        print("  Fault 1 needs more load: raise --fault1-concurrency, or reseed with a larger DEMO_ORDERS_ROWS.")
    return 0 if all(reproduced.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
