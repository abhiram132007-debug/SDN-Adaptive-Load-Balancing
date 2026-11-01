# Classroom Demo Guide — SDN Adaptive Load Balancer

This guide is for a presenter who has not run the project before. It gives the exact order of terminals and a short explanation to say during the demonstration.

## Before the demonstration

- Use an Ubuntu machine with Mininet, Open vSwitch, Python 3.9 for Ryu, and `psutil` installed.
- Clone the repository and follow the setup section in the root `README.md` once.
- Keep the repository root as the working directory.
- Open three terminal windows/tabs. Do not close a terminal while its component is running.

## Start the system — exact terminal order

### Terminal 1: Ryu controller

```bash
cd ~/SDN-Adaptive-Load-Balancing
source ~/ryu-env/bin/activate
ryu-manager --verbose source/lb_controller.py
```

Wait for the WSGI server message on port 8080. Ryu also listens for OpenFlow on port 6633.

### Terminal 2: Mininet topology and TCP servers

```bash
cd ~/SDN-Adaptive-Load-Balancing
sudo mn -c
sudo python3 source/topography.py
```

At `mininet>` run:

```text
nodes
net
pingall
```

Point out the three clients (`h1`, `h2`, `h3`), switch (`s1`) and servers (`srv1`, `srv2`, `srv3`). The topology starts the TCP server processes automatically.

### Terminal 3: Telemetry

```bash
cd ~/SDN-Adaptive-Load-Balancing
sudo python3 source/telemetry_sender.py --interval 1 --log /tmp/telemetry_log.jsonl
```

Show that each server reports UP and that latency/CPU/connection metrics are updated.

### Terminal 4: Controller state

```bash
curl -s http://127.0.0.1:8080/state | python3 -m json.tool
```

Point out the `mode`, `vip`, `servers`, per-server score, CPU, memory, latency, telemetry age and network-port measurements.

## Demonstration 1 — Round Robin

In Terminal 4:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"round_robin"}'
```

At `mininet>`:

```text
h1 python3 source/client.py --clients 20 --requests 1
```

Explain: Round Robin distributes new TCP connections sequentially across the available servers. The client prints the number of successes/failures, latency, throughput and server distribution.

## Demonstration 2 — Adaptive load balancing

In Terminal 4:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"adaptive"}'
```

At `mininet>`:

```text
h1 python3 source/client.py --clients 20 --requests 1
```

Explain: The telemetry sender reports CPU, memory, active connections, latency and network utilisation. The Adaptive strategy scores healthy servers and prefers the server with the lowest score.

## Demonstration 3 — Three client hosts

At `mininet>` run:

```text
h1 python3 source/client.py --clients 10 --requests 2 --label h1_demo
h2 python3 source/client.py --clients 10 --requests 2 --label h2_demo
h3 python3 source/client.py --clients 10 --requests 2 --label h3_demo
```

Explain: All three Mininet client nodes can send TCP requests to the same VIP.

## Demonstration 4 — Overloaded server detection

Set Adaptive mode and a temporary, low demonstration threshold. The controller's normal default connection threshold is 30; the value of 3 below is only to make the classroom test trigger quickly.

In Terminal 4:

```bash
curl -X POST http://127.0.0.1:8080/mode -H "Content-Type: application/json" -d '{"mode":"adaptive"}'
curl -X POST http://127.0.0.1:8080/config -H "Content-Type: application/json" -d '{"conn_high":3}'
```

At `mininet>` start sustained direct load on Server 2:

```text
h1 python3 source/client.py --target 10.0.0.12 --clients 40 --requests 100 --gap 0.05 --timeout 10 > /tmp/srv2_overload_test.txt 2>&1 &
```

In Terminal 4, poll status:

```bash
curl -s http://127.0.0.1:8080/state | python3 -c "import json,sys; d=json.load(sys.stdin); [print(s['name'],s['status'],s['active_flows']) for s in d['servers']]"
```

Wait until `server2 OVERLOADED` appears. While the direct load is still active, go to `mininet>` and make new VIP requests:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label overload_exclusion
```

Explain: The overloaded server is excluded from new Adaptive VIP connections. The test should show successful requests handled by Server 1 and Server 3, with no new VIP request assigned to Server 2.

Stop the direct-load process from `mininet>`:

```text
h1 pkill -f "client.py --target 10.0.0.12"
```

Wait for Server 2 to return to UP. Restore the original threshold in Terminal 4:

```bash
curl -X POST http://127.0.0.1:8080/config -H "Content-Type: application/json" -d '{"conn_high":30}'
```

## Demonstration 5 — Server failure and recovery

At `mininet>`:

```text
srv2 pkill -f "server.py --name srv2"
```

In Terminal 4 check `/state` and show Server 2 becomes DOWN. Then, at `mininet>`:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label server2_down
```

Explain: Adaptive avoids the failed server and new connections use the healthy servers.

Restart Server 2 from `mininet>`:

```text
srv2 python3 "$(pwd)/source/server.py" --name srv2 --delay 0.20 > /tmp/srv2.log 2>&1 &
```

Wait until `/state` reports Server 2 as UP. Run another VIP test:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label server2_recovered
```

Explain: Telemetry detects the recovered server and it becomes eligible for new Adaptive connections again.

## Performance results to show

The saved test set used 5 requests per client. The following are measured single-run results from the repository, not guaranteed values on every machine.

| Clients | Strategy | Average latency (ms) | P95 latency (ms) | Throughput (req/s) | Failures |
|---:|---|---:|---:|---:|---:|
| 10 | Round Robin | 347.7 | 853.2 | 19.56 | 0 |
| 10 | Adaptive | 310.1 | 804.7 | 22.39 | 0 |
| 25 | Round Robin | 501.0 | 1356.2 | 30.64 | 0 |
| 25 | Adaptive | 452.1 | 1354.6 | 35.14 | 0 |
| 50 | Round Robin | 751.8 | 2254.6 | 39.09 | 0 |
| 50 | Adaptive | 642.6 | 2054.6 | 44.86 | 0 |

The four graphs are in `graphs/`: `average_latency.png`, `p95_latency.png`, `throughput.png` and `server_distribution.png`.

## Short explanation for the evaluator

> “Clients connect to a Virtual IP. The Ryu controller receives server and network telemetry through a REST API. It supports Round Robin as a baseline and Adaptive load balancing, which selects the healthy server with the lowest load score. The controller detects unavailable or overloaded servers and avoids assigning them new Adaptive connections. When a server recovers, telemetry reports it as UP and it can rejoin the server pool. We tested concurrent clients, compared latency and throughput at 10, 25 and 50 clients, and demonstrated overload, failure and recovery scenarios.”

## Stop the demo

1. Press `Ctrl+C` in the telemetry terminal.
2. Type `exit` at the Mininet `mininet>` prompt.
3. Press `Ctrl+C` in the Ryu terminal.
4. Run `sudo mn -c` if cleanup is needed.
