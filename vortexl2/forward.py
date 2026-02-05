"""
VortexL2 Port Forward Management

Handles asyncio-based TCP port forwarding with better reliability and control.
Uses pure Python async I/O instead of socat for better error handling and logging.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

FORWARDS_STATE_FILE = Path("/var/lib/vortexl2/forwards.json")
FORWARDS_LOG_DIR = Path("/var/log/vortexl2")


@dataclass
class ForwardSession:
    """Represents an active port forwarding session."""
    port: int
    remote_ip: str
    remote_port: int
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    bytes_sent: int = 0
    bytes_received: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "port": self.port,
            "remote_ip": self.remote_ip,
            "remote_port": self.remote_port,
            "created_at": self.created_at,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
        }


class ForwardServer:
    """Manages a single port forward server using asyncio."""

    def __init__(self, port: int, remote_ip: str, remote_port: Optional[int] = None):
        self.port = int(port)
        self.remote_ip = remote_ip
        self.remote_port = int(remote_port) if remote_port is not None else int(port)

        self.server: Optional[asyncio.AbstractServer] = None
        self.running: bool = False
        self.active_sessions: List[ForwardSession] = []

        self.stats: Dict[str, int] = {
            "connections": 0,
            "total_bytes_sent": 0,
            "total_bytes_received": 0,
            "errors": 0,
        }

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                    session: ForwardSession, direction: str) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break

                if direction == "client->remote":
                    session.bytes_sent += len(data)
                    self.stats["total_bytes_sent"] += len(data)
                else:
                    session.bytes_received += len(data)
                    self.stats["total_bytes_received"] += len(data)

                writer.write(data)
                await writer.drain()
        except Exception as e:
            logger.debug("Pipe error (%s) on port %s: %s", direction, self.port, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def handle_client(self, local_reader: asyncio.StreamReader, local_writer: asyncio.StreamWriter) -> None:
        client_addr = local_writer.get_extra_info("peername")
        session = ForwardSession(port=self.port, remote_ip=self.remote_ip, remote_port=self.remote_port)
        self.active_sessions.append(session)
        self.stats["connections"] += 1

        remote_reader: Optional[asyncio.StreamReader] = None
        remote_writer: Optional[asyncio.StreamWriter] = None

        try:
            logger.info("Forward client %s connected on :%s -> %s:%s",
                        client_addr, self.port, self.remote_ip, self.remote_port)

            try:
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(self.remote_ip, self.remote_port),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                self.stats["errors"] += 1
                logger.error("Timeout connecting to %s:%s for local port %s",
                             self.remote_ip, self.remote_port, self.port)
                return
            except Exception as e:
                self.stats["errors"] += 1
                logger.error("Failed to connect to %s:%s for local port %s: %s",
                             self.remote_ip, self.remote_port, self.port, e)
                return

            t1 = asyncio.create_task(self._pipe(local_reader, remote_writer, session, "client->remote"))
            t2 = asyncio.create_task(self._pipe(remote_reader, local_writer, session, "remote->client"))
            await asyncio.gather(t1, t2)

        except Exception as e:
            self.stats["errors"] += 1
            logger.error("Error handling client %s on port %s: %s", client_addr, self.port, e)
        finally:
            # best-effort close
            try:
                local_writer.close()
                await local_writer.wait_closed()
            except Exception:
                pass

            try:
                if remote_writer:
                    remote_writer.close()
                    await remote_writer.wait_closed()
            except Exception:
                pass

            try:
                self.active_sessions.remove(session)
            except ValueError:
                pass

            logger.info("Forward client %s disconnected from :%s", client_addr, self.port)

    async def start(self) -> bool:
        """Start the forward server (runs forever until stopped)."""
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                host="0.0.0.0",
                port=self.port,
                reuse_address=True,
            )
            self.running = True
            logger.info("Forward server listening on 0.0.0.0:%s -> %s:%s",
                        self.port, self.remote_ip, self.remote_port)

            async with self.server:
                await self.server.serve_forever()
            return True
        except asyncio.CancelledError:
            # normal when daemon stops
            return True
        except OSError as e:
            self.running = False
            self.stats["errors"] += 1
            logger.error("Failed to bind/listen on port %s: %s", self.port, e)
            return False
        except Exception as e:
            self.running = False
            self.stats["errors"] += 1
            logger.error("Forward server error on port %s: %s", self.port, e)
            return False

    async def stop(self) -> None:
        """Stop the forward server."""
        self.running = False
        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
            except Exception:
                pass
        logger.info("Forward server stopped on port %s", self.port)

    def get_status(self) -> Dict[str, object]:
        return {
            "port": self.port,
            "remote": f"{self.remote_ip}:{self.remote_port}",
            "running": self.running,
            "active_sessions": len(self.active_sessions),
            "stats": self.stats,
        }


class ForwardManager:
    """Manages all port forwarding servers for a single tunnel config."""

    def __init__(self, config):
        self.config = config
        self.servers: Dict[int, ForwardServer] = {}

        # Ensure log dir exists (even if we don't write files here)
        FORWARDS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        FORWARDS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    def create_forward(self, port: int) -> Tuple[bool, str]:
        remote_ip = getattr(self.config, "remote_forward_ip", None)
        if not remote_ip:
            return False, "Remote forward IP not configured"

        port = int(port)
        if port in self.servers:
            return False, f"Port {port} already forwarding"

        self.servers[port] = ForwardServer(port, remote_ip, remote_port=port)

        # Persist in config
        self.config.add_port(port)
        return True, f"Port forward for {port} created (-> {remote_ip}:{port})"

    def remove_forward(self, port: int) -> Tuple[bool, str]:
        port = int(port)

        # Remove server object (daemon will recreate from config if still present)
        if port in self.servers:
            del self.servers[port]

        # Persist in config
        self.config.remove_port(port)
        return True, f"Port forward for {port} removed"

    def add_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        results: List[str] = []
        ports = [p.strip() for p in (ports_str or "").split(",") if p.strip()]

        for port_str in ports:
            try:
                port = int(port_str)
                success, msg = self.create_forward(port)
                results.append(f"Port {port}: {msg}")
            except ValueError:
                results.append(f"Port '{port_str}': Invalid port number")

        return True, "\n".join(results) if results else "No ports provided"

    def remove_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        results: List[str] = []
        ports = [p.strip() for p in (ports_str or "").split(",") if p.strip()]

        for port_str in ports:
            try:
                port = int(port_str)
                success, msg = self.remove_forward(port)
                results.append(f"Port {port}: {msg}")
            except ValueError:
                results.append(f"Port '{port_str}': Invalid port number")

        return True, "\n".join(results) if results else "No ports provided"

    def list_forwards(self) -> List[Dict[str, object]]:
        """List all configured port forwards with their status."""
        forwards: List[Dict[str, object]] = []
        listening_ports = self._get_listening_ports()

        remote_ip = getattr(self.config, "remote_forward_ip", None) or "-"

        for port in getattr(self.config, "forwarded_ports", []):
            port = int(port)
            is_running = port in listening_ports

            if port in self.servers:
                status = self.servers[port].get_status()
                # reflect actual system state
                status["running"] = is_running
                forwards.append(status)
            else:
                forwards.append({
                    "port": port,
                    "remote": f"{remote_ip}:{port}",
                    "running": is_running,
                    "active_sessions": 0,
                })

        return forwards

    def _get_listening_ports(self) -> set:
        """Best-effort: detect which ports this python process family is listening on."""
        import subprocess

        try:
            result = subprocess.run(
                "ss -tlnp | grep python | grep -oP ':\\K[0-9]+(?=\\s)'",
                shell=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                ports = set()
                for line in result.stdout.strip().split("\n"):
                    try:
                        ports.add(int(line.strip()))
                    except ValueError:
                        pass
                return ports
        except Exception:
            pass

        return set()

    async def start_all_forwards(self) -> Tuple[bool, str]:
        """Start all configured port forwards asynchronously (non-blocking)."""
        results: List[str] = []
        remote_ip = getattr(self.config, "remote_forward_ip", None)

        if not getattr(self.config, "forwarded_ports", []):
            return True, "No port forwards configured"

        if not remote_ip:
            return False, "Remote forward IP not configured"

        for port in self.config.forwarded_ports:
            port = int(port)

            if port not in self.servers:
                self.servers[port] = ForwardServer(port, remote_ip, remote_port=port)

            server = self.servers[port]
            if server.running:
                results.append(f"Port {port}: already running")
                continue

            # Start server task; it will run forever
            asyncio.create_task(server.start())
            results.append(f"Port {port}: starting...")

        return True, "\n".join(results)

    async def stop_all_forwards(self) -> Tuple[bool, str]:
        """Stop all forward servers created in this manager."""
        results: List[str] = []

        tasks = []
        for port, server in list(self.servers.items()):
            if server.running:
                tasks.append(server.stop())
                results.append(f"Port {port}: stopping...")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return True, "\n".join(results) if results else "No port forwards running"

    async def restart_all_forwards(self) -> Tuple[bool, str]:
        await self.stop_all_forwards()
        await asyncio.sleep(1)
        return await self.start_all_forwards()
