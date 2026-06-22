"""
MCP 连接测试脚本 - 模拟 CherryStudio 的 MCP 握手流程
"""
import subprocess
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
SERVER = str(PROJECT_ROOT / "server.py")

print("Launching server.py as subprocess...")
proc = subprocess.Popen(
    [VENV_PYTHON, SERVER],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0,
)

def send(msg):
    data = json.dumps(msg) + "\n"
    proc.stdin.write(data.encode("utf-8"))
    proc.stdin.flush()

def recv(timeout=15):
    line = b""
    start = time.time()
    while time.time() - start < timeout:
        ch = proc.stdout.read(1)
        if not ch:
            return None  # EOF
        if ch == b"\n":
            return json.loads(line.decode("utf-8"))
        line += ch
    return "TIMEOUT"

# Step 1: Initialize
print("\n=== Step 1: Sending initialize ===")
send({
    "jsonrpc": "2.0",
    "id": "1",
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "CherryStudio", "version": "1.0"}
    }
})
resp = recv(timeout=15)
if resp is None:
    print("FAIL: Connection closed (EOF) during initialize")
    stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
    print("STDERR:", stderr_out[-2000:])
    sys.exit(1)
elif resp == "TIMEOUT":
    print("FAIL: Timeout waiting for initialize response")
    sys.exit(1)
else:
    print("OK: Initialize response received")
    if "result" in resp:
        info = resp["result"].get("serverInfo", {})
        print(f"  Server: {info.get('name', '?')} v{info.get('version', '?')}")
        caps = resp["result"].get("capabilities", {})
        print(f"  Capabilities: {list(caps.keys())}")
    elif "error" in resp:
        print(f"  ERROR: {resp['error']}")
        sys.exit(1)

# Step 2: Send initialized notification
print("\n=== Step 2: Sending initialized notification ===")
send({
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
    "params": {}
})
time.sleep(1)
print("OK: Notification sent")

# Check if process is still alive
if proc.poll() is not None:
    print(f"FAIL: Process exited with code {proc.returncode}")
    stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
    print("STDERR:", stderr_out[-2000:])
    sys.exit(1)
print("OK: Process still running")

# Step 3: List tools
print("\n=== Step 3: Listing tools ===")
send({
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tools/list",
    "params": {}
})
resp2 = recv(timeout=10)
if resp2 and isinstance(resp2, dict) and "result" in resp2:
    tools = resp2["result"].get("tools", [])
    print(f"OK: Found {len(tools)} tools:")
    for t in tools:
        desc = t.get("description", "")[:60]
        print(f"  - {t['name']}: {desc}")
elif resp2 is None:
    print("FAIL: Connection closed during tools/list")
    sys.exit(1)
else:
    print(f"FAIL: Unexpected response: {resp2}")
    sys.exit(1)

# Step 4: Call qq_check_status
print("\n=== Step 4: Calling qq_check_status ===")
send({
    "jsonrpc": "2.0",
    "id": "3",
    "method": "tools/call",
    "params": {"name": "qq_check_status", "arguments": {}}
})
resp3 = recv(timeout=10)
if resp3 and isinstance(resp3, dict) and "result" in resp3:
    content = resp3["result"].get("content", [])
    for item in content:
        if item.get("type") == "text":
            print(f"OK: {item['text']}")
elif resp3 is None:
    print("FAIL: Connection closed during tools/call")
    sys.exit(1)
else:
    print(f"Response: {json.dumps(resp3, ensure_ascii=False)[:500]}")

# Step 5: Call qq_get_group_list (should fail gracefully if NapCat not connected)
print("\n=== Step 5: Calling qq_get_group_list ===")
send({
    "jsonrpc": "2.0",
    "id": "4",
    "method": "tools/call",
    "params": {"name": "qq_get_group_list", "arguments": {}}
})
resp4 = recv(timeout=10)
if resp4 and isinstance(resp4, dict) and "result" in resp4:
    content = resp4["result"].get("content", [])
    for item in content:
        if item.get("type") == "text":
            text = item["text"][:200]
            print(f"OK: {text}")
elif resp4 is None:
    print("FAIL: Connection closed")
    sys.exit(1)
else:
    print(f"Response: {json.dumps(resp4, ensure_ascii=False)[:500]}")

# Cleanup
proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()

print("\n=== All MCP tests passed! ===")
