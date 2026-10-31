#!/usr/bin/env python3
r"""
topography.py - Mininet topology for the adaptive SDN load balancer.

Run (Ryu must already be running):   sudo python3 topography.py

          h1  h2  h3   (clients)
            \  |  /
             [ s1 ] ---- root namespace (10.0.0.254)  <- telemetry + REST access
            /  |  \
        srv1 srv2 srv3 (servers, started automatically with different speeds)

Clients talk to the virtual IP 10.0.0.100 (answered by the controller).
Ports on s1: h1=1 h2=2 h3=3 srv1=4 srv2=5 srv3=6 root=7  (must match lb_controller.py)
"""
import os

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import Node, OVSKernelSwitch, RemoteController

HERE = os.path.dirname(os.path.abspath(__file__))
CONTROLLER_IP = '127.0.0.1'
CONTROLLER_PORT = 6633          # ryu-manager default

# name, ip, mac, switch port, simulated delay (s)  -> deliberately unequal servers
SERVER_DEFS = [
    ('srv1', '10.0.0.11', '00:00:00:00:00:11', 4, 0.05),   # fast
    ('srv2', '10.0.0.12', '00:00:00:00:00:12', 5, 0.20),   # medium
    ('srv3', '10.0.0.13', '00:00:00:00:00:13', 6, 0.50),   # slow
]
CLIENT_DEFS = [
    ('h1', '10.0.0.1', '00:00:00:00:00:01', 1),
    ('h2', '10.0.0.2', '00:00:00:00:00:02', 2),
    ('h3', '10.0.0.3', '00:00:00:00:00:03', 3),
]


def create_network():
    net = Mininet(controller=None, switch=OVSKernelSwitch, link=TCLink,
                  build=False, ipBase='10.0.0.0/8', autoSetMacs=False)

    info('*** Adding remote controller\n')
    net.addController('c0', controller=RemoteController,
                      ip=CONTROLLER_IP, port=CONTROLLER_PORT)

    info('*** Adding OpenFlow 1.3 switch\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    info('*** Adding clients\n')
    for name, ip, mac, port in CLIENT_DEFS:
        h = net.addHost(name, ip=ip, mac=mac)
        net.addLink(h, s1, port2=port)

    info('*** Adding servers\n')
    servers = []
    for name, ip, mac, port, delay in SERVER_DEFS:
        h = net.addHost(name, ip=ip, mac=mac)
        net.addLink(h, s1, port2=port, bw=10)   # 10 Mbps cap so congestion is real
        servers.append((h, name, delay))

    info('*** Connecting root namespace (telemetry/REST access)\n')
    root = Node('root', inNamespace=False)
    root_intf = net.addLink(root, s1, port2=7).intf1

    info('*** Starting network\n')
    net.start()
    root.setIP('10.0.0.254/8', intf=root_intf)
    try:
        net.waitConnected()
    except Exception:
        pass

    info('*** Starting TCP application servers\n')
    for h, name, delay in servers:
        h.cmd('python3 %s/server.py --name %s --delay %s > /tmp/%s.log 2>&1 &'
              % (HERE, name, delay, name))
        info('    %s started (delay=%ss), log: /tmp/%s.log\n' % (name, delay, name))

    info('\n*** Try:  h1 python3 client.py --clients 20   (clients target VIP 10.0.0.100)\n')
    info('*** Kill a server:  srv2 pkill -f "server.py --name srv2"\n')
    info('*** Controller state: curl http://127.0.0.1:8080/state\n\n')
    CLI(net)

    info('*** Stopping network\n')
    os.system('pkill -f "server.py --name" 2>/dev/null')
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    create_network()
