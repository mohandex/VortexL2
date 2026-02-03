"""
VortexL2 L2TPv3 Tunnel Management

Handles L2TPv3 tunnel and session creation/deletion using iproute2.
"""

import subprocess
import re
from typing import Optional, Dict, Tuple, List
from dataclasses import dataclass


# L2TPv3 IDs per role
TUNNEL_CONFIG = {
    "KHAREJ": {
        "tunnel_id": 2000,
        "peer_tunnel_id": 1000,
        "session_id": 20,
        "peer_session_id": 10,
    },
    "IRAN": {
        "tunnel_id": 1000,
        "peer_tunnel_id": 2000,
        "session_id": 10,
        "peer_session_id": 20,
    },
}

INTERFACE_NAME = "l2tpeth0"


@dataclass
class CommandResult:
    """Result of a shell command execution."""
    success: bool
    stdout: str
    stderr: str
    returncode: int


def run_command(cmd: str, check: bool = False) -> CommandResult:
    """Execute a shell command and return result."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return CommandResult(
            success=(result.returncode == 0),
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            returncode=result.returncode
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            success=False,
            stdout="",
            stderr="Command timed out",
            returncode=-1
        )
    except Exception as e:
        return CommandResult(
            success=False,
            stdout="",
            stderr=str(e),
            returncode=-1
        )


class TunnelManager:
    """Manages L2TPv3 tunnel and session operations."""
    
    def __init__(self, config):
        self.config = config
    
    def install_prerequisites(self) -> Tuple[bool, str]:
        """Install required packages and load kernel modules."""
        steps = []
        
        # Get kernel version
        result = run_command("uname -r")
        if not result.success:
            return False, "Failed to get kernel version"
        kernel_version = result.stdout.strip()
        
        # Install linux-modules-extra
        steps.append(f"Installing linux-modules-extra-{kernel_version}...")
        result = run_command(f"apt-get install -y linux-modules-extra-{kernel_version}")
        if not result.success:
            # Try without specific version as fallback
            result = run_command("apt-get install -y linux-modules-extra-$(uname -r)")
            if not result.success:
                steps.append(f"Warning: Could not install modules package: {result.stderr}")
        else:
            steps.append("Package installed successfully")
        
        # Install iproute2 with l2tp support
        result = run_command("apt-get install -y iproute2")
        if not result.success:
            steps.append(f"Warning: Could not install iproute2: {result.stderr}")
        
        # Load kernel modules
        modules = ["l2tp_core", "l2tp_netlink", "l2tp_eth"]
        for module in modules:
            steps.append(f"Loading module {module}...")
            result = run_command(f"modprobe {module}")
            if not result.success:
                return False, f"Failed to load module {module}: {result.stderr}"
            steps.append(f"Module {module} loaded")
        
        # Verify modules are loaded
        result = run_command("lsmod | grep l2tp")
        if "l2tp" not in result.stdout:
            return False, "L2TP modules not found in lsmod"
        
        steps.append("All prerequisites installed successfully!")
        return True, "\n".join(steps)
    
    def check_tunnel_exists(self, tunnel_id: int = None) -> bool:
        """Check if L2TP tunnel exists."""
        if tunnel_id is None:
            role = self.config.role
            if not role:
                return False
            tunnel_id = TUNNEL_CONFIG[role]["tunnel_id"]
        
        result = run_command("ip l2tp show tunnel")
        if not result.success:
            return False
        
        # Parse output for tunnel_id
        pattern = rf"Tunnel\s+{tunnel_id},"
        return bool(re.search(pattern, result.stdout))
    
    def check_session_exists(self, tunnel_id: int = None, session_id: int = None) -> bool:
        """Check if L2TP session exists."""
        if tunnel_id is None or session_id is None:
            role = self.config.role
            if not role:
                return False
            tunnel_id = TUNNEL_CONFIG[role]["tunnel_id"]
            session_id = TUNNEL_CONFIG[role]["session_id"]
        
        result = run_command("ip l2tp show session")
        if not result.success:
            return False
        
        # Parse output for session_id in tunnel
        pattern = rf"Session\s+{session_id}\s+in\s+tunnel\s+{tunnel_id}"
        return bool(re.search(pattern, result.stdout))
    
    def create_tunnel(self) -> Tuple[bool, str]:
        """Create L2TP tunnel based on configured role."""
        role = self.config.role
        if not role:
            return False, "Role not configured. Please configure endpoints first."
        
        if not self.config.ip_iran or not self.config.ip_kharej:
            return False, "IPs not configured. Please configure endpoints first."
        
        ids = TUNNEL_CONFIG[role]
        local_ip = self.config.get_local_ip()
        remote_ip = self.config.get_remote_ip()
        
        if self.check_tunnel_exists():
            return False, f"Tunnel {ids['tunnel_id']} already exists. Delete it first or use recreate."
        
        cmd = (
            f"ip l2tp add tunnel "
            f"tunnel_id {ids['tunnel_id']} "
            f"peer_tunnel_id {ids['peer_tunnel_id']} "
            f"encap ip "
            f"local {local_ip} "
            f"remote {remote_ip}"
        )
        
        result = run_command(cmd)
        if not result.success:
            return False, f"Failed to create tunnel: {result.stderr}"
        
        return True, f"Tunnel {ids['tunnel_id']} created successfully"
    
    def create_session(self) -> Tuple[bool, str]:
        """Create L2TP session in existing tunnel."""
        role = self.config.role
        if not role:
            return False, "Role not configured"
        
        ids = TUNNEL_CONFIG[role]
        
        if not self.check_tunnel_exists():
            return False, "Tunnel does not exist. Create tunnel first."
        
        if self.check_session_exists():
            return False, f"Session {ids['session_id']} already exists"
        
        cmd = (
            f"ip l2tp add session "
            f"tunnel_id {ids['tunnel_id']} "
            f"session_id {ids['session_id']} "
            f"peer_session_id {ids['peer_session_id']}"
        )
        
        result = run_command(cmd)
        if not result.success:
            return False, f"Failed to create session: {result.stderr}"
        
        return True, f"Session {ids['session_id']} created successfully"
    
    def bring_up_interface(self) -> Tuple[bool, str]:
        """Bring up the l2tpeth0 interface."""
        # Wait a moment for interface to appear
        import time
        time.sleep(0.5)
        
        result = run_command(f"ip link set {INTERFACE_NAME} up")
        if not result.success:
            return False, f"Failed to bring up interface: {result.stderr}"
        
        return True, f"Interface {INTERFACE_NAME} is up"
    
    def assign_ip(self, ip_cidr: str = None) -> Tuple[bool, str]:
        """Assign IP address to l2tpeth0 (Iran side only)."""
        if self.config.role != "IRAN":
            return False, "IP assignment is only for IRAN side"
        
        if ip_cidr is None:
            ip_cidr = self.config.iran_iface_ip
        
        # Check if IP already assigned
        result = run_command(f"ip addr show {INTERFACE_NAME}")
        if ip_cidr.split('/')[0] in result.stdout:
            return True, f"IP {ip_cidr} already assigned"
        
        result = run_command(f"ip addr add {ip_cidr} dev {INTERFACE_NAME}")
        if not result.success:
            # Check if it's because address exists
            if "RTNETLINK answers: File exists" in result.stderr:
                return True, f"IP {ip_cidr} already assigned"
            return False, f"Failed to assign IP: {result.stderr}"
        
        return True, f"IP {ip_cidr} assigned to {INTERFACE_NAME}"
    
    def delete_session(self) -> Tuple[bool, str]:
        """Delete L2TP session."""
        role = self.config.role
        if not role:
            return False, "Role not configured"
        
        ids = TUNNEL_CONFIG[role]
        
        if not self.check_session_exists():
            return True, "Session does not exist (already deleted)"
        
        cmd = f"ip l2tp del session tunnel_id {ids['tunnel_id']} session_id {ids['session_id']}"
        result = run_command(cmd)
        if not result.success:
            return False, f"Failed to delete session: {result.stderr}"
        
        return True, f"Session {ids['session_id']} deleted"
    
    def delete_tunnel(self) -> Tuple[bool, str]:
        """Delete L2TP tunnel (must delete session first)."""
        role = self.config.role
        if not role:
            return False, "Role not configured"
        
        ids = TUNNEL_CONFIG[role]
        
        # First delete session if exists
        if self.check_session_exists():
            success, msg = self.delete_session()
            if not success:
                return False, f"Failed to delete session first: {msg}"
        
        if not self.check_tunnel_exists():
            return True, "Tunnel does not exist (already deleted)"
        
        cmd = f"ip l2tp del tunnel tunnel_id {ids['tunnel_id']}"
        result = run_command(cmd)
        if not result.success:
            return False, f"Failed to delete tunnel: {result.stderr}"
        
        return True, f"Tunnel {ids['tunnel_id']} deleted"
    
    def full_setup(self) -> Tuple[bool, str]:
        """Perform full tunnel setup: create tunnel, session, bring up interface, assign IP."""
        steps = []
        
        # Create tunnel
        success, msg = self.create_tunnel()
        steps.append(f"Create tunnel: {msg}")
        if not success and "already exists" not in msg:
            return False, "\n".join(steps)
        
        # Create session
        success, msg = self.create_session()
        steps.append(f"Create session: {msg}")
        if not success and "already exists" not in msg:
            return False, "\n".join(steps)
        
        # Bring up interface
        success, msg = self.bring_up_interface()
        steps.append(f"Bring up interface: {msg}")
        if not success:
            return False, "\n".join(steps)
        
        # Assign IP (Iran side only)
        if self.config.role == "IRAN":
            success, msg = self.assign_ip()
            steps.append(f"Assign IP: {msg}")
            if not success:
                return False, "\n".join(steps)
        
        steps.append("\n✓ Tunnel setup complete!")
        return True, "\n".join(steps)
    
    def full_teardown(self) -> Tuple[bool, str]:
        """Perform full tunnel teardown: delete session and tunnel."""
        steps = []
        
        # Delete session
        success, msg = self.delete_session()
        steps.append(f"Delete session: {msg}")
        
        # Delete tunnel
        success, msg = self.delete_tunnel()
        steps.append(f"Delete tunnel: {msg}")
        
        steps.append("\n✓ Tunnel teardown complete!")
        return True, "\n".join(steps)
    
    def get_status(self) -> Dict[str, any]:
        """Get comprehensive tunnel status."""
        status = {
            "role": self.config.role,
            "configured": self.config.is_configured(),
            "tunnel_exists": False,
            "session_exists": False,
            "interface_up": False,
            "interface_ip": None,
            "tunnel_info": "",
            "session_info": "",
            "interface_info": "",
        }
        
        if not self.config.role:
            return status
        
        # Check tunnel
        result = run_command("ip l2tp show tunnel")
        status["tunnel_info"] = result.stdout if result.success else result.stderr
        status["tunnel_exists"] = self.check_tunnel_exists()
        
        # Check session
        result = run_command("ip l2tp show session")
        status["session_info"] = result.stdout if result.success else result.stderr
        status["session_exists"] = self.check_session_exists()
        
        # Check interface
        result = run_command(f"ip addr show {INTERFACE_NAME} 2>/dev/null")
        if result.success and result.stdout:
            status["interface_info"] = result.stdout
            status["interface_up"] = "UP" in result.stdout
            # Extract IP
            ip_match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+/\d+)', result.stdout)
            if ip_match:
                status["interface_ip"] = ip_match.group(1)
        
        return status
