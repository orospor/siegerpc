from __future__ import annotations

import argparse
import os
import queue
import signal
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
import urllib3

from .cli import Counter, RateLimiter, Result, drain_results, progress_line, summarize, write_csv


@dataclass(frozen=True)
class FormConfig:
    fields: dict[str, str]
    file_field: str
    file_path: Path
    filename: str
    mime_type: str


def parse_size(value: str) -> int:
    raw = value.strip().lower()
    multiplier = 1
    for suffix, factor in (("kb", 1024), ("mb", 1024 * 1024), ("gb", 1024 * 1024 * 1024)):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            multiplier = factor
            break
    try:
        size = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid size: {value}") from exc
    if size <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return int(size * multiplier)


def generate_payload_file(size_bytes: int) -> Path:
    handle = tempfile.NamedTemporaryFile(prefix="siegemax-", suffix=".bin", delete=False)
    path = Path(handle.name)
    chunk = b"0" * min(size_bytes, 1024 * 1024)
    remaining = size_bytes
    with handle:
        while remaining > 0:
            take = min(remaining, len(chunk))
            handle.write(chunk[:take])
            remaining -= take
    return path


def parse_field(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--field must use name=value")
    name, field_value = value.split("=", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("field name cannot be empty")
    return name, field_value


def build_form_config(args: argparse.Namespace) -> tuple[FormConfig, bool]:
    generated = False
    if args.file:
        file_path = Path(args.file).expanduser()
    else:
        file_path = generate_payload_file(args.generate_size)
        generated = True

    if not file_path.is_file():
        raise SystemExit(f"error: upload file not found: {file_path}")

    file_size = file_path.stat().st_size
    if file_size > args.max_file_size and not args.allow_large_file:
        raise SystemExit(
            f"error: file is {file_size} bytes, above --max-file-size. "
            "Use --allow-large-file only for authorized capacity tests."
        )

    fields = {
        "_wpcf7": args.form_id,
        "_wpcf7_version": args.cf7_version,
        "_wpcf7_locale": args.locale,
        "_wpcf7_unit_tag": args.unit_tag,
        "your-name": args.name,
        "your-email": args.email,
        "your-subject": args.subject,
        "your-message": args.message,
    }
    for name, value in args.field:
        fields[name] = value

    filename = args.filename or file_path.name
    return (
        FormConfig(
            fields=fields,
            file_field=args.file_field,
            file_path=file_path,
            filename=filename,
            mime_type=args.mime_type,
        ),
        generated,
    )


def post_once(
    session: requests.Session,
    url: str,
    form: FormConfig,
    timeout: float,
    verify_tls: bool,
    ok_status: set[int],
) -> Result:
    start = time.perf_counter()
    try:
        with form.file_path.open("rb") as upload:
            files = {name: (None, value) for name, value in form.fields.items()}
            files[form.file_field] = (form.filename, upload, form.mime_type)
            response = session.post(url, files=files, timeout=timeout, verify=verify_tls)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Result(
            ok=response.status_code in ok_status,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            bytes_read=len(response.content),
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return Result(False, None, elapsed_ms, 0, f"{type(exc).__name__}: {exc}")


def worker(
    worker_id: int,
    args: argparse.Namespace,
    form: FormConfig,
    limiter: RateLimiter,
    counter: Counter,
    stop_at: float,
    stopped: threading.Event,
    results: queue.Queue[Result],
) -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": f"{args.user_agent} worker/{worker_id}"})
    ok_status = set(args.ok_status)

    while not stopped.is_set():
        if args.duration and time.monotonic() >= stop_at:
            return
        if not counter.take():
            return
        limiter.wait()
        results.put(post_once(session, args.url, form, args.timeout, not args.insecure_tls, ok_status))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="siegemax",
        description="Authorized WordPress Contact Form 7 multipart upload load tester.",
    )
    parser.add_argument("--url", required=True, help="Contact Form 7 feedback endpoint URL")
    parser.add_argument("--file", help="File to upload on every request")
    parser.add_argument("--generate-size", type=parse_size, default=parse_size("7mb"), help="Generated file size when --file is omitted")
    parser.add_argument("--max-file-size", type=parse_size, default=parse_size("25mb"), help="Safety ceiling for upload file size")
    parser.add_argument("--allow-large-file", action="store_true", help="Allow files larger than --max-file-size")
    parser.add_argument("--form-id", default="50", help="Contact Form 7 form id")
    parser.add_argument("--cf7-version", default="6.1.6", help="Contact Form 7 version field")
    parser.add_argument("--locale", default="en_US", help="Contact Form 7 locale field")
    parser.add_argument("--unit-tag", default="wpcf7-f50-p30-o1", help="Contact Form 7 unit tag field")
    parser.add_argument("--file-field", default="file", help="Multipart file field name")
    parser.add_argument("--filename", help="Filename sent in multipart upload")
    parser.add_argument("--mime-type", default="text/plain", help="Multipart upload MIME type")
    parser.add_argument("--name", default="Test", help="Default your-name field")
    parser.add_argument("--email", default="test@test.com", help="Default your-email field")
    parser.add_argument("--subject", default="Test", help="Default your-subject field")
    parser.add_argument("--message", default="Hello", help="Default your-message field")
    parser.add_argument("--field", action="append", type=parse_field, default=[], help="Extra or override form field as name=value")
    parser.add_argument("--duration", type=float, default=30, help="Test duration in seconds")
    parser.add_argument("--requests", type=int, help="Stop after this many requests")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of worker threads")
    parser.add_argument("--rate", type=float, default=1, help="Global request rate limit. Use 0 for unlimited")
    parser.add_argument("--timeout", type=float, default=120, help="Per-request timeout in seconds")
    parser.add_argument("--ok-status", type=int, action="append", default=[200], help="HTTP status counted as OK. Repeatable")
    parser.add_argument("--progress-interval", type=float, default=1, help="Seconds between live progress updates")
    parser.add_argument("--csv", help="Optional CSV output path for per-request results")
    parser.add_argument("--user-agent", default="siegemax/0.1 authorized-upload-test", help="Base User-Agent header")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument(
        "--i-own-this-server",
        action="store_true",
        help="Required confirmation that you own or are authorized to test the target",
    )
    args = parser.parse_args(argv)

    if not args.i_own_this_server:
        parser.error("refusing to run without --i-own-this-server")
    parsed = urlparse(args.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        parser.error("--url must be a valid http:// or https:// URL")
    if "/wp-json/contact-form-7/" not in parsed.path:
        print("warning: URL does not look like a Contact Form 7 feedback endpoint", file=sys.stderr)
    if args.concurrency < 1 or args.concurrency > 100:
        parser.error("--concurrency must be between 1 and 100")
    if args.duration is not None and args.duration <= 0 and args.requests is None:
        parser.error("--duration must be positive unless --requests is set")
    if args.requests is not None and args.requests < 1:
        parser.error("--requests must be positive")
    if args.rate is not None and args.rate < 0:
        parser.error("--rate cannot be negative")
    if args.progress_interval <= 0:
        parser.error("--progress-interval must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.insecure_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    form, generated_file = build_form_config(args)
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

    print(f"siegemax target:     {args.url}")
    print(f"form id:             {args.form_id}")
    print(f"upload file:         {form.file_path} ({form.file_path.stat().st_size} bytes)")
    print(f"file field:          {form.file_field}")
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
            args=(index + 1, args, form, limiter, counter, stop_at, stopped, results_queue),
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
    final_interval = max(time.perf_counter() - next_progress + args.progress_interval, 0.001)
    print(progress_line(results, started, previous_total, final_interval), flush=True)

    if args.csv:
        write_csv(args.csv, results)
        print(f"csv:                 {args.csv}")

    if generated_file:
        try:
            os.unlink(form.file_path)
        except OSError:
            pass

    print(summarize(results, elapsed))
    return 0 if results and any(item.ok for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
