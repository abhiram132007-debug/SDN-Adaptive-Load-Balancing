"""
lb_controller.py  -  SDN load-balancing controller (Ryu, OpenFlow 1.3)

Run:   ryu-manager lb_controller.py
REST:  http://<controller>:8080/...

What it does
  * Clients connect to a virtual IP (VIP). The controller answers ARP for the VIP,
    picks a backend server for every new TCP connection and installs flows that
    rewrite VIP <-> server addresses in the switch (so data never touches the controller).
  * Two strategies (switch at runtime with POST /mode):
      round_robin : STATIC baseline. Cycles through all servers, ignores health/load.
      adaptive    : picks the healthy server with the lowest load score.
  * Fault tolerance (adaptive mode): servers that report DOWN / stop sending telemetry
    are excluded and their flows are deleted; OVERLOADED servers are excluded for new
    connections until they recover (with hysteresis).
  * Other traffic (ping, telemetry probes, ARP between hosts) is handled as a learning switch.

REST API
  GET  /state
  POST /metrics/server   {"server":"server1","status":"UP","cpu_percent":20,"memory_mb":40,
                          "active_conns":3,"latency_ms":120}
  POST /metrics/network  {"port":4,"rx_mbps":1.2,"tx_mbps":3.4}
  POST /mode             {"mode":"round_robin"}  or  {"mode":"adaptive"}
  POST /config           {"weights":{"conn":0.5},"cpu_high":90,"stale_sec":5, ...}
  POST /reset            (clears counters, flows and connection table)

Assumes ONE switch (s1) and the topology in topography.py. Keep the constants below in sync.
"""
import json
import time

from webob import Response
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet, tcp
from ryu.ofproto import ofproto_v1_3

# ----------------------------------------------------------------------------
# Static configuration (must match topography.py)
# ----------------------------------------------------------------------------
VIP = '10.0.0.100'
VIP_MAC = '00:00:00:00:00:64'

SERVERS = [
    {'name': 'server1', 'alias': 'srv1', 'ip': '10.0.0.11', 'mac': '00:00:00:00:00:11', 'port': 4},
    {'name': 'server2', 'alias': 'srv2', 'ip': '10.0.0.12', 'mac': '00:00:00:00:00:12', 'port': 5},
    {'name': 'server3', 'alias': 'srv3', 'ip': '10.0.0.13', 'mac': '00:00:00:00:00:13', 'port': 6},
]

MODE_RR = 'round_robin'
MODE_ADAPTIVE = 'adaptive'

UP = 'UP'
DOWN = 'DOWN'
OVERLOADED = 'OVERLOADED'

PRIO_MISS = 0
PRIO_L2 = 1
PRIO_LB = 10
ALL_BITS = 0xFFFFFFFFFFFFFFFF
LB_INSTANCE = 'lb_app'


class LoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(LoadBalancer, self).__init__(*args, **kwargs)
        wsgi = kwargs['wsgi']
        wsgi.register(LBRest, {LB_INSTANCE: self})

        self.datapaths = {}
        self.mac_to_port = {}
        self.mode = MODE_RR
        self.rr_next = 0
        self.conn_map = {}      # (client_ip, client_port, dst_port) -> {'idx', 'ts'}
        self.net = {}           # switch port -> {'rx_mbps', 'tx_mbps', 'ts'}

        self.cfg = {
            'ema_alpha': 0.5,
            'stale_sec': 10.0,      # no telemetry for this long => DOWN
            'cpu_high': 85.0,       # percent
            'lat_high': 800.0,      # ms (EMA)
            'conn_high': 30,        # active flows
            'link_mbps': 100.0,     # assumed link capacity for utilisation
            'flow_idle_timeout': 5,
            'weights': {'conn': 0.4, 'lat': 0.3, 'cpu': 0.2, 'net': 0.1},
            'norm': {'conn': 10.0, 'lat': 500.0},
        }

        self.srv = []
        for _ in SERVERS:
            self.srv.append({
                'status': UP, 'reported': None, 'last_seen': None,
                'cpu': None, 'mem_mb': None, 'rep_conns': 0,
                'lat_ema': None, 'lat_last': None,
                'flows': 0,      # installed LB flows (from flow stats)
                'pending': 0,    # assigned since last flow-stats poll
                'total': 0,      # total connections assigned
            })
        self._tmp_flows = [0] * len(SERVERS)

        hub.spawn(self._monitor_loop)

    # ------------------------------------------------------------------
    # OpenFlow plumbing
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        self.mac_to_port.setdefault(dp.id, {})
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, PRIO_MISS, match, actions)
        self.logger.info('Switch %s connected', dp.id)

    def _add_flow(self, dp, priority, match, actions, idle=0, hard=0, cookie=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(
            datapath=dp, priority=priority, match=match, instructions=inst,
            idle_timeout=idle, hard_timeout=hard, cookie=cookie))

    def _packet_out(self, dp, in_port, actions, data):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port,
            actions=actions, data=data))

    def _delete_lb_flows(self, idx=None):
        """Delete LB flows (forward cookie=i+1, reverse cookie=0x100+i+1)."""
        idxs = range(len(SERVERS)) if idx is None else [idx]
        for dp in self.datapaths.values():
            ofp, parser = dp.ofproto, dp.ofproto_parser
            for i in idxs:
                for cookie in (i + 1, 0x100 + i + 1):
                    dp.send_msg(parser.OFPFlowMod(
                        datapath=dp, command=ofp.OFPFC_DELETE,
                        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                        cookie=cookie, cookie_mask=ALL_BITS,
                        match=parser.OFPMatch()))

    # ------------------------------------------------------------------
    # Packet-in
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port

        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is not None:
            if arp_pkt.opcode == arp.ARP_REQUEST and arp_pkt.dst_ip == VIP:
                self._send_arp_reply(dp, in_port, eth, arp_pkt)
                return
            self._l2_forward(dp, msg, in_port, eth)
            return

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is not None and ip_pkt.dst == VIP:
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            if tcp_pkt is not None:
                self._handle_vip_tcp(dp, msg, in_port, eth, ip_pkt, tcp_pkt)
            return  # non-TCP traffic to the VIP is dropped

        self._l2_forward(dp, msg, in_port, eth)

    def _send_arp_reply(self, dp, in_port, eth, req):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        e = ethernet.ethernet(dst=eth.src, src=VIP_MAC, ethertype=ether_types.ETH_TYPE_ARP)
        a = arp.arp(opcode=arp.ARP_REPLY, src_mac=VIP_MAC, src_ip=VIP,
                    dst_mac=req.src_mac, dst_ip=req.src_ip)
        p = packet.Packet()
        p.add_protocol(e)
        p.add_protocol(a)
        p.serialize()
        self._packet_out(dp, ofp.OFPP_CONTROLLER,
                         [parser.OFPActionOutput(in_port)], p.data)

    def _l2_forward(self, dp, msg, in_port, eth):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src)
            self._add_flow(dp, PRIO_L2, match, actions, idle=30)
        self._packet_out(dp, in_port, actions, msg.data)

    # ------------------------------------------------------------------
    # Load balancing
    # ------------------------------------------------------------------
    def _handle_vip_tcp(self, dp, msg, in_port, eth, ip_pkt, tcp_pkt):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        now = time.time()
        key = (ip_pkt.src, tcp_pkt.src_port, tcp_pkt.dst_port)
        is_syn = bool(tcp_pkt.bits & tcp.TCP_SYN)
        entry = self.conn_map.get(key)

        if entry is not None and not is_syn:
            idx = entry['idx']            # flows expired mid-connection: keep same server
        elif is_syn:
            idx = self._select_server()
            if idx is None:
                self.logger.warning('No available server for %s:%s', ip_pkt.src, tcp_pkt.src_port)
                return
            self.srv[idx]['pending'] += 1
            self.srv[idx]['total'] += 1
            self.logger.info('[%s] %s:%s -> %s', self.mode, ip_pkt.src,
                             tcp_pkt.src_port, SERVERS[idx]['name'])
        else:
            return                         # stray non-SYN packet, unknown connection

        self.conn_map[key] = {'idx': idx, 'ts': now}
        server = SERVERS[idx]
        idle = self.cfg['flow_idle_timeout']

        # client -> VIP  becomes  client -> server
        fwd_match = parser.OFPMatch(
            in_port=in_port, eth_type=ether_types.ETH_TYPE_IP, ip_proto=6,
            ipv4_src=ip_pkt.src, ipv4_dst=VIP,
            tcp_src=tcp_pkt.src_port, tcp_dst=tcp_pkt.dst_port)
        fwd_actions = [
            parser.OFPActionSetField(eth_dst=server['mac']),
            parser.OFPActionSetField(ipv4_dst=server['ip']),
            parser.OFPActionOutput(server['port'])]

        # server -> client  becomes  VIP -> client
        rev_match = parser.OFPMatch(
            in_port=server['port'], eth_type=ether_types.ETH_TYPE_IP, ip_proto=6,
            ipv4_src=server['ip'], ipv4_dst=ip_pkt.src,
            tcp_src=tcp_pkt.dst_port, tcp_dst=tcp_pkt.src_port)
        rev_actions = [
            parser.OFPActionSetField(eth_src=VIP_MAC),
            parser.OFPActionSetField(ipv4_src=VIP),
            parser.OFPActionOutput(in_port)]

        self._add_flow(dp, PRIO_LB, fwd_match, fwd_actions, idle=idle, cookie=idx + 1)
        self._add_flow(dp, PRIO_LB, rev_match, rev_actions, idle=idle, cookie=0x100 + idx + 1)
        self._packet_out(dp, in_port, fwd_actions, msg.data)

    def _select_server(self):
        n = len(SERVERS)
        if self.mode == MODE_RR:
            idx = self.rr_next % n
            self.rr_next += 1
            return idx
        cands = [i for i in range(n) if self.srv[i]['status'] == UP]
        if not cands:
            cands = [i for i in range(n) if self.srv[i]['status'] == OVERLOADED]
        if not cands:
            return None
        return min(cands, key=lambda i: (self._score(i), self.srv[i]['total']))

    def _active(self, i):
        s = self.srv[i]
        return max(s['flows'] + s['pending'], s['rep_conns'] or 0)

    def _net_util(self, i):
        n = self.net.get(SERVERS[i]['port'])
        if not n or time.time() - n['ts'] > 10:
            return 0.0
        return min(1.0, max(n['rx_mbps'], n['tx_mbps']) / self.cfg['link_mbps'])

    def _score(self, i):
        """Lower is better.  score = w1*conns + w2*latency + w3*cpu + w4*link_util (all normalised)."""
        s, w, nm = self.srv[i], self.cfg['weights'], self.cfg['norm']
        return (w['conn'] * self._active(i) / nm['conn']
                + w['lat'] * (s['lat_ema'] or 0.0) / nm['lat']
                + w['cpu'] * (s['cpu'] or 0.0) / 100.0
                + w['net'] * self._net_util(i))

    # ------------------------------------------------------------------
    # Health / monitoring loop
    # ------------------------------------------------------------------
    def _monitor_loop(self):
        while True:
            hub.sleep(1)
            try:
                self._update_health()
                self._request_flow_stats()
            except Exception as exc:     # never let the loop die
                self.logger.error('monitor loop error: %s', exc)

    def _compute_status(self, s, now):
        c = self.cfg
        if s['last_seen'] is not None:
            if now - s['last_seen'] > c['stale_sec'] or s['reported'] == DOWN:
                return DOWN
        f = 0.8 if s['status'] == OVERLOADED else 1.0      # hysteresis
        over = ((s['cpu'] is not None and s['cpu'] > c['cpu_high'] * f)
                or (s['lat_ema'] is not None and s['lat_ema'] > c['lat_high'] * f)
                or (s['flows'] + s['pending'] > c['conn_high'] * f))
        return OVERLOADED if over else UP

    def _update_health(self):
        now = time.time()
        for i, s in enumerate(self.srv):
            new = self._compute_status(s, now)
            if new == s['status']:
                continue
            self.logger.info('%s: %s -> %s', SERVERS[i]['name'], s['status'], new)
            s['status'] = new
            if new == DOWN and self.mode == MODE_ADAPTIVE:
                self._delete_lb_flows(i)
                self.conn_map = {k: v for k, v in self.conn_map.items() if v['idx'] != i}
                s['flows'] = s['pending'] = 0
        for k in [k for k, v in self.conn_map.items() if now - v['ts'] > 300]:
            del self.conn_map[k]

    def _request_flow_stats(self):
        for dp in self.datapaths.values():
            ofp, parser = dp.ofproto, dp.ofproto_parser
            dp.send_msg(parser.OFPFlowStatsRequest(
                dp, 0, ofp.OFPTT_ALL, ofp.OFPP_ANY, ofp.OFPG_ANY, 0, 0, parser.OFPMatch()))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply(self, ev):
        msg = ev.msg
        for stat in msg.body:
            if 1 <= stat.cookie <= len(SERVERS):
                self._tmp_flows[stat.cookie - 1] += 1
        if msg.flags & msg.datapath.ofproto.OFPMPF_REPLY_MORE:
            return
        for i, s in enumerate(self.srv):
            s['flows'] = self._tmp_flows[i]
            s['pending'] = 0
        self._tmp_flows = [0] * len(SERVERS)

    # ------------------------------------------------------------------
    # Methods used by the REST layer
    # ------------------------------------------------------------------
    def _lookup(self, key):
        for i, s in enumerate(SERVERS):
            if key in (s['name'], s['alias'], s['ip']):
                return i
        return None

    def update_server(self, data):
        idx = self._lookup(data.get('server') or data.get('ip'))
        if idx is None:
            return False
        s = self.srv[idx]
        s['last_seen'] = time.time()
        s['reported'] = str(data.get('status', UP)).upper()
        cpu = data.get('cpu', data.get('cpu_percent'))
        mem = data.get('mem_mb', data.get('memory_mb'))
        if cpu is not None:
            s['cpu'] = float(cpu)
        if mem is not None:
            s['mem_mb'] = float(mem)
        if data.get('active_conns') is not None:
            s['rep_conns'] = int(data['active_conns'])
        lat = data.get('latency_ms')
        if s['reported'] == DOWN:
            s['lat_ema'] = s['lat_last'] = None
        elif lat is not None:
            lat = float(lat)
            a = self.cfg['ema_alpha']
            s['lat_last'] = lat
            s['lat_ema'] = lat if s['lat_ema'] is None else a * lat + (1 - a) * s['lat_ema']
        return True

    def update_network(self, data):
        port = int(data['port'])
        self.net[port] = {'rx_mbps': float(data.get('rx_mbps') or 0.0),
                          'tx_mbps': float(data.get('tx_mbps') or 0.0),
                          'ts': time.time()}

    def set_mode(self, mode):
        if mode not in (MODE_RR, MODE_ADAPTIVE):
            return False
        self.mode = mode
        self.rr_next = 0
        self.conn_map.clear()
        self._delete_lb_flows()
        self.logger.info('Mode set to %s', mode)
        return True

    def update_config(self, body):
        for k, v in body.items():
            if k not in self.cfg:
                continue
            if isinstance(self.cfg[k], dict) and isinstance(v, dict):
                for kk, vv in v.items():
                    if kk in self.cfg[k]:
                        self.cfg[k][kk] = float(vv)
            elif not isinstance(self.cfg[k], dict):
                self.cfg[k] = type(self.cfg[k])(v)

    def reset(self):
        self.conn_map.clear()
        self.rr_next = 0
        self._delete_lb_flows()
        for s in self.srv:
            s['flows'] = s['pending'] = s['total'] = 0

    def state(self):
        now = time.time()
        servers = []
        for i, s in enumerate(self.srv):
            servers.append({
                'name': SERVERS[i]['name'], 'ip': SERVERS[i]['ip'], 'status': s['status'],
                'score': round(self._score(i), 3), 'active_flows': self._active(i),
                'total_assigned': s['total'], 'cpu': s['cpu'], 'mem_mb': s['mem_mb'],
                'latency_ema_ms': None if s['lat_ema'] is None else round(s['lat_ema'], 1),
                'telemetry_age_s': None if s['last_seen'] is None else round(now - s['last_seen'], 1),
            })
        return {'mode': self.mode, 'vip': VIP, 'servers': servers,
                'network': {str(p): v for p, v in self.net.items()}, 'config': self.cfg}


# ----------------------------------------------------------------------------
# REST API
# ----------------------------------------------------------------------------
def _resp(obj, status=200):
    return Response(content_type='application/json', status=status,
                    body=json.dumps(obj).encode('utf-8'))


def _body(req):
    try:
        return json.loads(req.body.decode('utf-8') or '{}')
    except ValueError:
        return None


class LBRest(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(LBRest, self).__init__(req, link, data, **config)
        self.app = data[LB_INSTANCE]

    @route('lb_state', '/state', methods=['GET'])
    def get_state(self, req, **kw):
        return _resp(self.app.state())

    @route('lb_msrv', '/metrics/server', methods=['POST'])
    def post_server(self, req, **kw):
        body = _body(req)
        if not isinstance(body, dict):
            return _resp({'error': 'invalid JSON'}, 400)
        if not self.app.update_server(body):
            return _resp({'error': 'unknown server'}, 404)
        return _resp({'ok': True})

    @route('lb_mnet', '/metrics/network', methods=['POST'])
    def post_network(self, req, **kw):
        body = _body(req)
        try:
            self.app.update_network(body)
        except (TypeError, KeyError, ValueError):
            return _resp({'error': 'need numeric "port"'}, 400)
        return _resp({'ok': True})

    @route('lb_mode', '/mode', methods=['POST'])
    def post_mode(self, req, **kw):
        body = _body(req)
        if not isinstance(body, dict) or not self.app.set_mode(body.get('mode')):
            return _resp({'error': 'mode must be round_robin or adaptive'}, 400)
        return _resp({'ok': True, 'mode': self.app.mode})

    @route('lb_cfg', '/config', methods=['POST'])
    def post_config(self, req, **kw):
        body = _body(req)
        if not isinstance(body, dict):
            return _resp({'error': 'invalid JSON'}, 400)
        self.app.update_config(body)
        return _resp({'ok': True, 'config': self.app.cfg})

    @route('lb_reset', '/reset', methods=['POST'])
    def post_reset(self, req, **kw):
        self.app.reset()
        return _resp({'ok': True})
