#!/usr/bin/env python3
"""
telemetry_sender.py - Member 2: collects server + network metrics and POSTs them to the controller.

Run in a normal terminal (NOT in the Mininet CLI) while the topology is up:
    sudo python3 telemetry_sender.py [--interval 1] [--controller http://127.0.0.1:8080]
(sudo is needed for ovs-ofctl; the root namespace reaches the servers via 10.0.0.254.)

Server metrics : GET /metrics (per-process CPU/mem/active conns) + GET /probe (service latency)
Network metrics: ovs-ofctl dump-ports s1  ->  per-port rx/tx Mbps
Everything is also appended to --log (JSON lines) for the report graphs.
"""
import argparse
import json
import re
import socket
import subprocess
import time
import urllib.request

SERVERS = {'server1': '10.0.0.11', 'server2': '10.0.0.12', 'server3': '10.0.0.13'}
SERVICE_PORT = 5000
SWITCH = 's1'


def http_get(ip, path, timeout=2.0):
    t0 = time.perf_counter()
    with socket.create_connection((ip, SERVICE_PORT), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n' % (path, ip)).encode())
        data = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b'\r\n\r\n')
    return head, body, (time.perf_counter() - t0) * 1000


def collect_server(name, ip):
    result = {'server': name, 'ip': ip, 'timestamp': time.time(), 'status': 'DOWN',
              'latency_ms': None, 'cpu_percent': None, 'memory_mb': None, 'active_conns': None}
    try:
        head, _, ms = http_get(ip, '/probe')
        if b'200 OK' not in head:
            return result
        result['latency_ms'] = round(ms, 2)
        head, body, _ = http_get(ip, '/metrics')
        if b'200 OK' in head:
            m = json.loads(body.decode())
            result['cpu_percent'] = m.get('cpu_percent')
            result['memory_mb'] = m.get('memory_mb')
            result['active_conns'] = m.get('active_conns')
            result['status'] = 'UP'
    except (OSError, ValueError):
        pass
    return result


def read_port_stats():
    cmd = ['ovs-ofctl', '-O', 'OpenFlow13', 'dump-ports', SWITCH]
    out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    stats, port = {}, None
    for line in out.splitlines():
        m = re.match(r'\s*port\s+(\S+):', line)
        if m:                                   # rx counters are on THIS line too
            port = m.group(1) if m.group(1).isdigit() else None
            if port:
                stats[port] = {'rx_bytes': 0, 'tx_bytes': 0, 'rx_packets': 0, 'tx_packets': 0}
        if port is None:
            continue
        for d in ('rx', 'tx'):
            m = re.search(r'%s\s+pkts=(\d+),\s*bytes=(\d+)' % d, line)
            if m:
                stats[port][d + '_packets'] = int(m.group(1))
                stats[port][d + '_bytes'] = int(m.group(2))
    return stats


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req, timeout=1).read()
        return True
    except Exception as exc:
        print('POST %s failed: %s' % (url, exc))
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--controller', default='http://127.0.0.1:8080')
    ap.add_argument('--log', default='telemetry_log.jsonl')
    args = ap.parse_args()

    previous = {}
    print('Telemetry sender started -> %s (Ctrl+C to stop)' % args.controller)
    try:
        with open(args.log, 'a') as log:
            while True:
                t_start = time.time()
                for name, ip in SERVERS.items():
                    r = collect_server(name, ip)
                    post(args.controller + '/metrics/server', r)
                    log.write(json.dumps(r) + '\n')
                    print('%s %s lat=%s cpu=%s conns=%s' % (
                        name, r['status'], r['latency_ms'], r['cpu_percent'], r['active_conns']))

                try:
                    now = time.time()
                    for port, v in read_port_stats().items():
                        rec = {'timestamp': now, 'switch': SWITCH, 'port': int(port),
                               'rx_mbps': 0.0, 'tx_mbps': 0.0, **v}
                        old = previous.get(port)
                        if old and now > old['time']:
                            for d in ('rx', 'tx'):
                                delta = max(0, v[d + '_bytes'] - old[d + '_bytes'])
                                rec[d + '_mbps'] = round(delta * 8 / (now - old['time']) / 1e6, 4)
                            post(args.controller + '/metrics/network', rec)
                        previous[port] = {'time': now, **v}
                        log.write(json.dumps(rec) + '\n')
                except (OSError, subprocess.CalledProcessError) as exc:
                    print('port stats unavailable: %s' % exc)

                log.flush()
                time.sleep(max(0.0, args.interval - (time.time() - t_start)))
    except KeyboardInterrupt:
        print('\nTelemetry sender stopped.')


if __name__ == '__main__':
    main()
