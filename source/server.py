#!/usr/bin/env python3
"""
server.py - multi-threaded TCP application server (raw sockets).

  python3 server.py --name srv1 --delay 0.05 [--port 5000] [--contention 0.1] [--cpu-ms 0]

  GET /metrics -> JSON {name, cpu_percent, memory_mb, active_conns}  (per-PROCESS values)
  anything else -> simulated work (delay + contention + optional CPU burn), replies
                   "Hello from <name>" so clients can see which server answered.

Needs psutil:  sudo apt install python3-psutil   (or: sudo pip3 install psutil)
"""
import argparse
import json
import os
import socket
import threading
import time

import psutil

active = 0
lock = threading.Lock()
proc = psutil.Process(os.getpid())
proc.cpu_percent(None)          # prime the counter


def burn_cpu(ms):
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        pass


def http_response(body, ctype='text/plain'):
    return (b'HTTP/1.1 200 OK\r\nContent-Type: ' + ctype.encode()
            + b'\r\nContent-Length: ' + str(len(body)).encode()
            + b'\r\nConnection: close\r\n\r\n' + body)


def handle_client(conn, addr, args):
    global active
    try:
        conn.settimeout(5)
        data = conn.recv(4096)
        if not data:
            return
        request = data.decode(errors='ignore')

        if request.startswith('GET /metrics'):
            with lock:
                n = active
            body = json.dumps({
                'name': args.name,
                'cpu_percent': proc.cpu_percent(None),
                'memory_mb': round(proc.memory_info().rss / (1024 * 1024), 2),
                'active_conns': n,
            }).encode()
            conn.sendall(http_response(body, 'application/json'))
            return

        with lock:
            active += 1
            n = active
        try:
            if args.cpu_ms:
                burn_cpu(args.cpu_ms)
            # busier server => slower server (makes load-awareness matter)
            time.sleep(args.delay * (1 + args.contention * (n - 1)))
            if args.verbose:
                print('[%s] request from %s (active=%d)' % (time.strftime('%H:%M:%S'), addr, n))
            conn.sendall(http_response(('Hello from %s' % args.name).encode()))
        finally:
            with lock:
                active -= 1
    except Exception as exc:
        print('Error: %s' % exc)
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='srv')
    ap.add_argument('--port', type=int, default=5000)
    ap.add_argument('--delay', type=float, default=0.1, help='base service time (s)')
    ap.add_argument('--contention', type=float, default=0.1,
                    help='extra slowdown per concurrent request')
    ap.add_argument('--cpu-ms', type=float, default=0.0, help='CPU burn per request (ms)')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', args.port))
    srv.listen(128)
    print('[*] %s listening on port %d (delay=%.3fs)' % (args.name, args.port, args.delay), flush=True)

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr, args), daemon=True).start()


if __name__ == '__main__':
    main()
