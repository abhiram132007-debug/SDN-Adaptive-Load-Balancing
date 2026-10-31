# Project Test Cases

## 1. Mininet Connectivity

Command:
pingall

Expected Result:
All hosts should communicate successfully.

Observed Result:
30/30 packets received.
0% packet loss.

## 2. TCP Client-Server Communication

Command:
h1 python3 client.py --clients 20

Observed Result:
20 requests completed successfully.
0 failed requests.

## 3. Round Robin Load Balancing

| Clients | Requests | Successful | Failed |
|---|---:|---:|---:|
| 10 | 50 | 50 | 0 |
| 25 | 125 | 125 | 0 |
| 50 | 250 | 250 | 0 |

## 4. Adaptive Load Balancing

| Clients | Requests | Successful | Failed |
|---|---:|---:|---:|
| 10 | 50 | 50 | 0 |
| 25 | 125 | 125 | 0 |
| 50 | 250 | 250 | 0 |

## 5. Server Failure Detection

Procedure:
Stop Server 2 while Adaptive mode is active.

Observed Result:
Server 2 was detected as DOWN.
New connections were distributed between Server 1 and Server 3.
All 20 client requests succeeded.

## 6. Server Recovery

Procedure:
Restart Server 2.

Observed Result:
Server 2 returned to UP status.
Telemetry detected the recovered server.
New client requests were distributed across all three servers.

## 7. Network Statistics

Command:
sudo ovs-ofctl -O OpenFlow13 dump-ports s1

Observed Result:
8 switch ports reported.
RX drops: 0
TX drops: 0
RX errors: 0
TX errors: 0

Note:
These are observed results from the captured test runs.
