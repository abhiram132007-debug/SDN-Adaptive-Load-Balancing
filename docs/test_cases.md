# Test Cases and Observed Results

The results below are from the project runs captured during implementation. Performance values are single-run measurements and may differ on another computer.

## TC-01 — Mininet connectivity

**Command at `mininet>`**

```text
pingall
```

**Observed:** `0% dropped (30/30 received)` in the recorded run.

## TC-02 — TCP application servers

**Topology:** `srv1`, `srv2` and `srv3` are started automatically by `source/topography.py`.

**Observed:** All three TCP servers started and reported `UP` to the telemetry sender.

## TC-03 — Concurrent TCP client requests

**Example command at `mininet>`**

```text
h1 python3 source/client.py --clients 20 --requests 1
```

**Observed:** 20 of 20 requests succeeded in the recorded baseline run.

## TC-04 — Round Robin performance

Each experiment used five requests per client thread.

| Concurrent clients | Requests | Successful | Failed | Average latency (ms) | P95 latency (ms) | Throughput (req/s) |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 50 | 50 | 0 | 347.7 | 853.2 | 19.56 |
| 25 | 125 | 125 | 0 | 501.0 | 1356.2 | 30.64 |
| 50 | 250 | 250 | 0 | 751.8 | 2254.6 | 39.09 |

## TC-05 — Adaptive performance

Each experiment used five requests per client thread.

| Concurrent clients | Requests | Successful | Failed | Average latency (ms) | P95 latency (ms) | Throughput (req/s) |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 50 | 50 | 0 | 310.1 | 804.7 | 22.39 |
| 25 | 125 | 125 | 0 | 452.1 | 1354.6 | 35.14 |
| 50 | 250 | 250 | 0 | 642.6 | 2054.6 | 44.86 |

## TC-06 — Server failure detection and traffic continuation

**Procedure:** In Adaptive mode, stop Server 2 from the Mininet prompt:

```text
srv2 pkill -f "server.py --name srv2"
```

Check the controller state with `GET /state`. Wait for telemetry to report Server 2 as `DOWN`, then run a VIP test:

```text
h1 python3 source/client.py --clients 20 --requests 1
```

**Observed:** Server 2 was detected as DOWN. All 20 VIP requests succeeded; the recorded distribution was Server 1 = 13, Server 3 = 7, with no requests assigned to Server 2.

## TC-07 — Server recovery

**Procedure:** Restart Server 2 from the Mininet prompt after the failure test:

```text
srv2 python3 "$(pwd)/source/server.py" --name srv2 --delay 0.20 > /tmp/srv2.log 2>&1 &
```

Wait for telemetry and check `GET /state` until Server 2 reports `UP`.

**Observed:** Server 2 returned to UP. In the recorded post-recovery VIP test, all 20 requests succeeded and the distribution was Server 1 = 10, Server 2 = 7, Server 3 = 3. The client output identified Server 2 by IP address `10.0.0.12` in that run.

## TC-08 — Overloaded-server detection and exclusion

**Purpose:** Demonstrate that Adaptive uses server-reported active TCP connections to identify an overloaded backend and avoids assigning it new VIP connections.

**Procedure:**

1. Set Adaptive mode.
2. Temporarily set `conn_high` to 3 to make the overload easier to trigger in a short classroom demonstration. The controller's normal default is 30; restore it to 30 after the test.
3. From `mininet>`, create sustained direct load on Server 2:

```text
h1 python3 source/client.py --target 10.0.0.12 --clients 40 --requests 100 --gap 0.05 --timeout 10 > /tmp/srv2_overload_test.txt 2>&1 &
```

4. Poll `GET /state` until Server 2 reports `OVERLOADED` and its active count exceeds the threshold.
5. While the direct load continues, send new connections through the VIP:

```text
h1 python3 source/client.py --clients 20 --requests 1 --label overload_exclusion
```

6. Stop the background direct-load client, wait for Server 2 to return to UP, and restore `conn_high` to 30.

**Observed:** The controller reported Server 2 as `OVERLOADED` with 38–40 active connections. The VIP exclusion test completed with 20 successful requests and 0 failures. Its recorded distribution was Server 1 = 13, Server 3 = 7; Server 2 received zero new VIP requests. After the direct load ended, all three servers returned to UP and telemetry was fresh.

## TC-09 — Open vSwitch port statistics

**Command in a normal Ubuntu terminal while Mininet is running:**

```bash
sudo ovs-ofctl -O OpenFlow13 dump-ports s1
```

**Observed in the captured port-statistics snapshot:** 8 switch ports; the displayed RX drops, TX drops, RX errors, TX errors and collisions were zero.

## Notes

- `h1`, `h2`, `h3`, `srv1`, `srv2` and `srv3` commands must be run at the Mininet `mininet>` prompt.
- `curl`, `ryu-manager`, `sudo mn -c` and `ovs-ofctl` commands are run in a normal Ubuntu terminal unless explicitly stated otherwise.
- The overload threshold of 3 is a temporary demonstration setting. Restore the normal value of 30 after the test.
- The server-failure test and overload test are different scenarios: failure reports `DOWN`; overload reports `OVERLOADED`.
