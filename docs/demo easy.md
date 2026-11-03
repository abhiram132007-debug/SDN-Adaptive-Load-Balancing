# Classroom Demo Guide — SDN Adaptive Load Balancer

Use this guide during the demo. Each grey code block is a separate copy-and-paste group. On GitHub, use the **Copy** icon at the top-right of a code block.

## Before starting

- Use the Ubuntu VM where Mininet, Open vSwitch, Python dependencies, and the `~/ryu-env` virtual environment are already installed.
- Clone the repository only once. If it is already cloned, skip the clone commands.
- Keep all four terminals open while their components are running.

### Clone the GitHub repository (only once)

```bash
cd ~
git clone https://github.com/abhiram132007-debug/SDN-Adaptive-Load-Balancing.git
```

If the folder already exists, do not run `git clone` again.

---

## Start the system

### Terminal 1 — Ryu controller

Copy and paste this entire block into Terminal 1:

```bash
cd ~/SDN-Adaptive-Load-Balancing
source ~/ryu-env/bin/activate
ryu-manager --verbose source/lb_controller.py
```

Wait until Ryu reports that the WSGI server is listening on port `8080`. Leave this terminal running. Do not press `Ctrl+C`.

### Terminal 2 — Mininet topology and TCP servers

Copy and paste this entire block into Terminal 2:

```bash
cd ~/SDN-Adaptive-Load-Balancing
sudo mn -c
sudo python3 source/topography.py
```

When the `mininet>` prompt appears, copy and paste these commands:

```text
nodes
net
pingall
```

Expected: the topology contains clients `h1`, `h2`, `h3`, switch `s1`, and servers `srv1`, `srv2`, `srv3`. `pingall` should report no packet loss. The topology starts the TCP server processes.

### Terminal 3 — Telemetry sender

Copy and paste this block into Terminal 3:

```bash
cd ~/SDN-Adaptive-Load-Balancing
sudo python3 source/telemetry_sender.py --interval 1 --log /tmp/telemetry_log.jsonl
```

Leave this terminal running. It sends server and network measurements to the Ryu controller.

### Terminal 4 — Controller status and mode commands

Copy and paste this block into Terminal 4 to view the controller state:

```bash
cd ~/SDN-Adaptive-Load-Balancing
curl -s http://127.0.0.1:8080/state | python3 -m json.tool
```

Expected: the three servers show `UP`, with telemetry values updating.

---

## Demonstration 1 — Round Robin

In Terminal 4, select Round Robin mode:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"round_robin"}'
```

At the `mininet>` prompt in Terminal 2, send client requests:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label rr_demo
```

Explain: Round Robin sends new TCP connections sequentially to the available servers.

## Demonstration 2 — Adaptive load balancing

In Terminal 4, select Adaptive mode:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"adaptive"}'
```

At the `mininet>` prompt in Terminal 2:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label adaptive_demo
```

Explain: Adaptive mode uses server and network telemetry to score healthy servers and prefer the server with the lowest load score.

## Demonstration 3 — Three client hosts

At the `mininet>` prompt in Terminal 2, run each line:

```text
h1 python3 source/client.py --clients 10 --requests 2 --label h1_demo
h2 python3 source/client.py --clients 10 --requests 2 --label h2_demo
h3 python3 source/client.py --clients 10 --requests 2 --label h3_demo
```

Explain: all three Mininet client hosts can send TCP requests to the same Virtual IP.

## Demonstration 4 — Overloaded-server detection

Use Adaptive mode and a temporary low threshold so the demonstration triggers quickly. The normal connection threshold is `30`; setting it to `3` is only for this demonstration.

In Terminal 4, copy and paste:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"adaptive"}'
curl -X POST http://127.0.0.1:8080/config -H "Content-Type: application/json" -d '{"conn_high":3}'
```

At the `mininet>` prompt in Terminal 2, start direct load against Server 2:

```text
h1 python3 source/client.py --target 10.0.0.12 --clients 40 --requests 100 --gap 0.05 --timeout 10 > /tmp/srv2_overload_test.txt 2>&1 &
```

In Terminal 4, check server status:

```bash
curl -s http://127.0.0.1:8080/state | python3 -c "import json,sys; d=json.load(sys.stdin); [print(s['name'],s['status'],s['active_flows']) for s in d['servers']]"
```

Wait until `server2 OVERLOADED` appears. While the direct load is still running, send new VIP requests at the `mininet>` prompt:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label overload_exclusion
```

Expected: new Adaptive VIP requests are assigned to Server 1 and Server 3, not the overloaded Server 2.

Stop the direct-load process at the `mininet>` prompt:

```text
h1 pkill -f "client.py --target 10.0.0.12"
```

Wait for Server 2 to return to `UP`. Then restore the normal threshold in Terminal 4:

```bash
curl -X POST http://127.0.0.1:8080/config -H "Content-Type: application/json" -d '{"conn_high":30}'
```

## Demonstration 5 — Server failure and recovery

At the `mininet>` prompt in Terminal 2, stop Server 2:

```text
srv2 pkill -f "server.py --name srv2"
```

In Terminal 4, check the controller state:

```bash
curl -s http://127.0.0.1:8080/state | python3 -m json.tool
```

Wait until Server 2 shows `DOWN`. Then, at the `mininet>` prompt:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label server2_down
```

Explain: Adaptive mode avoids the failed server and uses healthy servers for new connections.

Restart Server 2 at the `mininet>` prompt:

```text
srv2 python3 "$(pwd)/source/server.py" --name srv2 --delay 0.20 > /tmp/srv2.log 2>&1 &
```

Wait until the controller state shows Server 2 as `UP`. Then run:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label server2_recovered
```

Explain: telemetry detects the recovered server, allowing it to rejoin the pool for new Adaptive connections.

---

## Performance results to show

The saved performance test set used **5 requests per client thread**. These are measured results from the repository; individual runs can vary by machine.

| Clients | Strategy | Average latency (ms) | P95 latency (ms) | Throughput (req/s) | Failures |
|---:|---|---:|---:|---:|---:|
| 10 | Round Robin | 347.7 | 853.2 | 19.56 | 0 |
| 10 | Adaptive | 310.1 | 804.7 | 22.39 | 0 |
| 25 | Round Robin | 501.0 | 1356.2 | 30.64 | 0 |
| 25 | Adaptive | 452.1 | 1354.6 | 35.14 | 0 |
| 50 | Round Robin | 751.8 | 2254.6 | 39.09 | 0 |
| 50 | Adaptive | 642.6 | 2054.6 | 44.86 | 0 |

The four graphs are in `graphs/`: `average_latency.png`, `p95_latency.png`, `throughput.png`, and `server_distribution.png`.

## Short explanation for the evaluator

> Clients connect to a Virtual IP. The Ryu controller receives server and network telemetry through a REST API. It supports Round Robin as a baseline and Adaptive load balancing, which selects a healthy server using its load score. The controller detects unavailable or overloaded servers and avoids assigning them new Adaptive connections. When a server recovers, telemetry reports it as UP and it can rejoin the server pool. We compared latency and throughput at 10, 25, and 50 clients, and demonstrated multi-client traffic, overload, failure, and recovery scenarios.

## Stop the demo

1. Press `Ctrl+C` in Terminal 3 (telemetry).
2. Type `exit` at the `mininet>` prompt in Terminal 2.
3. Press `Ctrl+C` in Terminal 1 (Ryu).
4. If cleanup is needed, run this in a normal Ubuntu terminal:

```bash
sudo mn -c
```
