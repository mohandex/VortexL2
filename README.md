# VortexL2 (Fork by @mohandex)

**L2TPv3 Ethernet Tunnel Manager for Ubuntu/Debian**  
A production-ready CLI/TUI tool to manage multiple L2TPv3 (L2TP Ethernet) tunnels and per-tunnel TCP port forwarding.

> Security note: L2TPv3 provides **no encryption**. Use it for connectivity/overlay; add encryption separately if needed.

---

## Features

- Interactive TUI management panel (Rich)
- Multiple L2TPv3 tunnels on a single server
- Per-tunnel TCP port forwarding (async Python)
- systemd services for persistence:
  - `vortexl2-tunnel.service` (bring tunnels up on boot)
  - `vortexl2-forward-daemon.service` (run forwards on boot)
- Duplicate validation for tunnel IDs/session IDs/interface IPs
- **Auto private IP allocation**:
  - Pool: `10.30.0.0/16`
  - Each tunnel gets a unique `/30`
  - If Interface IP (or Remote Forward Target on IRAN) is left empty, VortexL2 auto-suggests and uses the next free `/30`

---

## Install

### Quick install

```bash
bash <(curl -Ls https://raw.githubusercontent.com/mohandex/VortexL2/main/install.sh)
```

### Manual install (dev)

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip iproute2
pip3 install -r requirements.txt
```

---

## First run

Open management panel:

```bash
sudo vortexl2
```

### 1) Install prerequisites

From the menu:
- `Install/Verify Prerequisites`

This loads required kernel modules:
- `l2tp_core`
- `l2tp_netlink`
- `l2tp_eth`

---

## Tunnel model (IRAN + multiple KHAREJ)

You can create **one IRAN** server with many **KHAREJ** servers (or any topology) by adding multiple tunnels.

Each tunnel has its own:
- L2TP IDs (tunnel_id / peer_tunnel_id / session_id / peer_session_id)
- Interface (l2tpeth0, l2tpeth1, …)
- Private point-to-point subnet (/30)
- Forwarded ports list (independent per tunnel)

### Auto private IP allocation (recommended)

When creating a tunnel, for these fields you can press Enter to auto-fill:
- `Interface IP`  → auto-assign from `10.30.0.0/16` (unique `/30` per tunnel)
- On IRAN side: `Remote Forward Target IP` → auto-sets to the peer IP of that `/30`

Allocation rules:
- Each tunnel uses one `/30`
- IRAN gets `.1/30`
- KHAREJ gets `.2/30`

---

## Create tunnel (step-by-step)

From the menu:
- `Create Tunnel`
- Choose side: `IRAN` or `KHAREJ`
- Enter:
  - Local server public IP (this server)
  - Remote server public IP (peer)
  - Interface IP (press Enter for auto)
  - IDs (can use defaults; must match across both sides)

### Matching both sides

For the same tunnel, IRAN and KHAREJ configs must mirror each other:

| Field | IRAN side | KHAREJ side |
|------|----------:|------------:|
| Local IP | IRAN public IP | KHAREJ public IP |
| Remote IP | KHAREJ public IP | IRAN public IP |
| Interface IP | `10.30... .1/30` | `10.30... .2/30` |
| Tunnel ID | A | B |
| Peer Tunnel ID | B | A |
| Session ID | C | D |
| Peer Session ID | D | C |

---

## Port forwarding (per-tunnel)

Port forwards are configured **per tunnel**.

On the IRAN server:
- `Port Forwards`
- Select the tunnel
- Add ports like: `443,80,2053`

The forward daemon will start listeners for that tunnel’s forwarded ports and forward to that tunnel’s `remote_forward_ip` (typically the KHAREJ /30 peer IP).

---

## Services

Check status:

```bash
sudo systemctl status vortexl2-tunnel
sudo systemctl status vortexl2-forward-daemon
```

Logs:

```bash
journalctl -u vortexl2-tunnel -f
journalctl -u vortexl2-forward-daemon -f
```

---

## Troubleshooting

### Check tunnel objects

```bash
ip l2tp show tunnel
ip l2tp show session
```

### Check interfaces

```bash
ip addr show l2tpeth0
ip addr show l2tpeth1
```

### Test P2P reachability

From IRAN:

```bash
ping <kharej_p2p_ip>
```

### Check forwarded listeners

```bash
ss -ltnp | grep python
```

---

## Configuration files

Tunnels are stored at:
- `/etc/vortexl2/tunnels/<name>.yaml`

Example:

```yaml
name: tunnel1
side: IRAN
local_ip: "1.2.3.4"
remote_ip: "5.6.7.8"
interface_ip: "10.30.0.1/30"
remote_forward_ip: "10.30.0.2"
tunnel_id: 1100
peer_tunnel_id: 2100
session_id: 11
peer_session_id: 21
interface_index: 0
forwarded_ports:
  - 443
  - 80
```

---

## Uninstall

```bash
sudo systemctl stop vortexl2-tunnel vortexl2-forward-daemon
sudo systemctl disable vortexl2-tunnel vortexl2-forward-daemon

sudo rm -rf /opt/vortexl2
sudo rm -f /usr/local/bin/vortexl2
sudo rm -f /etc/systemd/system/vortexl2-*
sudo rm -rf /etc/vortexl2
sudo rm -rf /var/lib/vortexl2
sudo rm -rf /var/log/vortexl2

sudo systemctl daemon-reload
```

---

## License

MIT
