from __future__ import annotations

import argparse
import csv
import queue
import signal
import ssl
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Iterable
from urllib.parse import urlparse

DEFAULT_BODY = """<?xml version="1.0"?>
<methodCall>
  <methodName>{method}</methodName>
  <params></params>
</methodCall>
"""


@dataclass(frozen=True)
class Result:
    ok: bool
    status: int | None
    elapsed_ms: float
    bytes_read: int
    error: str | None = None


class RateLimiter:
    def __init__(self, rate: float | None) -> None:
        self.rate = rate
        self.lock = threading.Lock()
        self.next_at = time.monotonic()

    def wait(self) -> None:
        if not self.rate or self.rate <= 0:
            return
        interval = 1.0 / self.rate
        with self.lock:
            now = time.monotonic()
            if self.next_at > now:
                sleep_for = self.next_at - now
                self.next_at += interval
            else:
                sleep_for = 0
                self.next_at = now + interval
        if sleep_for:
            time.sleep(sleep_for)


class Counter:
    def __init__(self, max_requests: int | None) -> None:
        self.max_requests = max_requests
        self.sent = 0
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            if self.max_requests is not None and self.sent >= self.max_requests:
                return False
            self.sent += 1
            return True


def xmlrpc_body(method: str) -> bytes:
    return DEFAULT_BODY.format(method=escape_xml(method)).encode("utf-8")


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def request_once(
    url: str,
    body: bytes,
    timeout: float,
    user_agent: str,
    insecure_tls: bool,
) -> Result:
    parsed = urlparse(url)
    path = parsed.path or "/xmlrpc.php"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    headers = {
        "Content-Type": "text/xml",
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Connection": "close",
        "Content-Length": str(len(body)),
    }

    start = time.perf_counter()
    conn: HTTPConnection | HTTPSConnection | None = None
    try:
        if parsed.scheme == "https":
            context = ssl._create_unverified_context() if insecure_tls else None
            conn = HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout, context=context)
        elif parsed.scheme == "http":
            conn = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        else:
            raise ValueError("URL must start with http:// or https://")

        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Result(
            ok=response.status < 500,
            status=response.status,
            elapsed_ms=elapsed_ms,
            bytes_read=len(data),
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Result(False, None, elapsed_ms, 0, f"{type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            conn.close()


def worker(
    worker_id: int,
    args: argparse.Namespace,
    body: bytes,
    limiter: RateLimiter,
    counter: Counter,
    stop_at: float,
    stopped: threading.Event,
    results: queue.Queue[Result],
) -> None:
    while not stopped.is_set():
        if args.duration and time.monotonic() >= stop_at:
            return
        if not counter.take():
            return
        limiter.wait()
        result = request_once(
            args.url,
            body,
            args.timeout,
            f"{args.user_agent} worker/{worker_id}",
            args.insecure_tls,
        )
        results.put(result)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return ordered[index]


def summarize(results: list[Result], elapsed: float) -> str:
    total = len(results)
    successes = sum(1 for item in results if item.ok)
    failures = total - successes
    bytes_read = sum(item.bytes_read for item in results)
    latencies = [item.elapsed_ms for item in results]

    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}
    for item in results:
        key = str(item.status) if item.status is not None else "client_error"
        statuses[key] = statuses.get(key, 0) + 1
        if item.error:
            errors[item.error] = errors.get(item.error, 0) + 1

    availability = (successes / total * 100) if total else 0
    rps = total / elapsed if elapsed > 0 else 0

    lines = [
        "",
        "Summary",
        "-------",
        f"requests:      {total}",
        f"successes:     {successes}",
        f"failures:      {failures}",
        f"availability:  {availability:.2f}%",
        f"elapsed:       {elapsed:.2f}s",
        f"throughput:    {rps:.2f} req/s",
        f"bytes read:    {bytes_read}",
    ]

    if latencies:
        lines.extend(
            [
                f"latency min:   {min(latencies):.2f} ms",
                f"latency mean:  {statistics.fmean(latencies):.2f} ms",
                f"latency p50:   {percentile(latencies, 50):.2f} ms",
                f"latency p95:   {percentile(latencies, 95):.2f} ms",
                f"latency p99:   {percentile(latencies, 99):.2f} ms",
                f"latency max:   {max(latencies):.2f} ms",
            ]
        )

    lines.append("")
    lines.append("Status")
    lines.append("------")
    for status, count in sorted(statuses.items()):
        lines.append(f"{status}: {count}")

    if errors:
        lines.append("")
        lines.append("Errors")
        lines.append("------")
        for error, count in sorted(errors.items(), key=lambda item: item[1], reverse=True)[:10]:
            lines.append(f"{count}x {error}")

    return "\n".join(lines)


def write_csv(path: str, results: Iterable[Result]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ok", "status", "elapsed_ms", "bytes_read", "error"])
        for item in results:
            writer.writerow([item.ok, item.status, f"{item.elapsed_ms:.3f}", item.bytes_read, item.error or ""])


def drain_results(results_queue: queue.Queue[Result], results: list[Result]) -> int:
    drained = 0
    while True:
        try:
            results.append(results_queue.get_nowait())
            drained += 1
        except queue.Empty:
            return drained


def progress_line(results: list[Result], started: float, previous_total: int, interval: float) -> str:
    elapsed = max(time.perf_counter() - started, 0.001)
    total = len(results)
    successes = sum(1 for item in results if item.ok)
    failures = total - successes
    current_rate = (total - previous_total) / max(interval, 0.001)
    average_rate = total / elapsed
    availability = (successes / total * 100) if total else 0
    recent = results[-100:]
    recent_latencies = [item.elapsed_ms for item in recent]
    recent_p95 = percentile(recent_latencies, 95) if recent_latencies else 0
    return (
        f"progress: {total} requests | ok {successes} | fail {failures} | "
        f"availability {availability:.2f}% | current {current_rate:.2f} req/s | "
        f"avg {average_rate:.2f} req/s | recent p95 {recent_p95:.2f} ms"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="siegerpc",
        description="Authorized WordPress xmlrpc.php load tester.",
    )
    parser.add_argument("--url", required=True, help="Target WordPress XML-RPC URL, e.g. https://example.com/xmlrpc.php")
    parser.add_argument("--method", default="system.listMethods", help="XML-RPC method name to call")
    parser.add_argument("--duration", type=float, default=30, help="Test duration in seconds")
    parser.add_argument("--requests", type=int, help="Stop after this many requests")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of worker threads")
    parser.add_argument("--rate", type=float, default=50, help="Global request rate limit. Use 0 for unlimited")
    parser.add_argument("--timeout", type=float, default=30, help="Per-request timeout in seconds")
    parser.add_argument("--progress-interval", type=float, default=1, help="Seconds between live progress updates")
    parser.add_argument("--csv", help="Optional CSV output path for per-request results")
    parser.add_argument("--user-agent", default="siegerpc/0.1 authorized-load-test", help="Base User-Agent header")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument(
        "--i-own-this-server",
        action="store_true",
        help="Required confirmation that you own or are authorized to test the target",
    )
    args = parser.parse_args(argv)

    if not args.i_own_this_server:
        parser.error("refusing to run without --i-own-this-server")
    if args.concurrency < 1 or args.concurrency > 500:
        parser.error("--concurrency must be between 1 and 500")
    if args.duration is not None and args.duration <= 0 and args.requests is None:
        parser.error("--duration must be positive unless --requests is set")
    if args.requests is not None and args.requests < 1:
        parser.error("--requests must be positive")
    if args.rate is not None and args.rate < 0:
        parser.error("--rate cannot be negative")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be positive")
    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("--url must be a valid http:// or https:// URL")
    if not parsed.path.endswith("xmlrpc.php"):
        print("warning: URL path does not end with xmlrpc.php", file=sys.stderr)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    body = xmlrpc_body(args.method)
    limiter = RateLimiter(args.rate)
    counter = Counter(args.requests)
    stopped = threading.Event()
    results_queue: queue.Queue[Result] = queue.Queue()
    stop_at = time.monotonic() + args.duration if args.duration else float("inf")
    threads: list[threading.Thread] = []

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print(f"siegerpc target:      {args.url}")
    print(f"method:              {args.method}")
    print(f"concurrency:         {args.concurrency}")
    print(f"rate limit:          {'unlimited' if args.rate == 0 else f'{args.rate:g} req/s'}")
    print(f"duration:            {args.duration:g}s" if args.duration else "duration:            request-count limited")
    if args.requests:
        print(f"request limit:       {args.requests}")
    print("press Ctrl+C to stop early", flush=True)

    started = time.perf_counter()
    for index in range(args.concurrency):
        thread = threading.Thread(
            target=worker,
            args=(index + 1, args, body, limiter, counter, stop_at, stopped, results_queue),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    results: list[Result] = []
    previous_total = 0
    next_progress = time.perf_counter() + args.progress_interval
    while any(thread.is_alive() for thread in threads):
        time.sleep(min(0.1, args.progress_interval))
        drain_results(results_queue, results)
        now = time.perf_counter()
        if now >= next_progress:
            print(progress_line(results, started, previous_total, args.progress_interval), flush=True)
            previous_total = len(results)
            next_progress = now + args.progress_interval

    for thread in threads:
        thread.join()

    elapsed = time.perf_counter() - started
    drain_results(results_queue, results)
    print(progress_line(results, started, previous_total, max(time.perf_counter() - next_progress + args.progress_interval, 0.001)), flush=True)

    if args.csv:
        write_csv(args.csv, results)
        print(f"csv:                 {args.csv}")

    print(summarize(results, elapsed))
    return 0 if results and any(item.ok for item in results) else 1
