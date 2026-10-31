#!/usr/bin/env python3
"""
client.py - concurrent TCP client / traffic generator.

  python3 client.py [--target 10.0.0.100] [--port 5000] [--clients 20]
                    [--requests 5] [--gap 0] [--out results.jsonl] [--label adaptive_20]

Every request opens a NEW TCP connection (so each one is load-balanced separately),
and records latency, success/failure and which server answered.
"""
import argparse
import json
import socket
import statistics
import threading
import time
from collections import Counter

results = []
res_lock = threading.Lock()


def one_request(target, port, timeout):
    t0 = time.perf_counter()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((target, port))
        s.sendall(b'GET / HTTP/1.1\r\nHost: vip\r\nConnection: close\r\n\r\n')
        data = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        body = data.partition(b'\r\n\r\n')[2].decode(errors='ignore')
        if not data.startswith(b'HTTP/1.1 200'):
            raise ValueError('bad response')
        return True, (time.perf_counter() - t0) * 1000, body.replace('Hello from ', '').strip()
    except Exception as exc:
        return False, (time.perf_counter() - t0) * 1000, type(exc).__name__


def worker(args):
    for _ in range(args.requests):
        ok, ms, info = one_request(args.target, args.port, args.timeout)
        with res_lock:
            results.append((ok, ms, info))
        if args.gap:
            time.sleep(args.gap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', default='10.0.0.100', help='VIP (default) or a server IP')
    ap.add_argument('--port', type=int, default=5000)
    ap.add_argument('--clients', type=int, default=10, help='concurrent client threads')
    ap.add_argument('--requests', type=int, default=1, help='requests per client thread')
    ap.add_argument('--gap', type=float, default=0.0, help='pause between requests (s)')
    ap.add_argument('--timeout', type=float, default=3.0)
    ap.add_argument('--out', default=None, help='append JSON summary to this file')
    ap.add_argument('--label', default='')
    args = ap.parse_args()

    print('[*] %d clients x %d requests -> %s:%d' %
          (args.clients, args.requests, args.target, args.port))
    start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(args,)) for _ in range(args.clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - start

    good = [r for r in results if r[0]]
    bad = [r for r in results if not r[0]]
    lat = sorted(r[1] for r in good)
    per_server = Counter(r[2] for r in good)

    summary = {
        'label': args.label, 'target': args.target, 'clients': args.clients,
        'requests_total': len(results), 'success': len(good), 'failed': len(bad),
        'avg_ms': round(statistics.mean(lat), 1) if lat else None,
        'p95_ms': round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        'max_ms': round(lat[-1], 1) if lat else None,
        'throughput_rps': round(len(good) / wall, 2) if wall > 0 else None,
        'duration_s': round(wall, 2),
        'per_server': dict(per_server),
        'errors': dict(Counter(r[2] for r in bad)),
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        with open(args.out, 'a') as f:
            f.write(json.dumps(summary) + '\n')


if __name__ == '__main__':
    main()
