"""Full MCP handshake test - simplified"""
import subprocess, json, time, os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
LAUNCHER = str(PROJECT_ROOT / "server.py")

print("=== Testing MCP full handshake ===")

proc = subprocess.Popen(
    [VENV_PYTHON, LAUNCHER],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env={**os.environ, "PYTHONUNBUFFERED": "1"},
)

# Build all messages
msgs = ""
msgs += json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}) + "\n"
msgs += json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n"
msgs += json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list"}) + "\n"

try:
    stdout, stderr = proc.communicate(input=msgs.encode(), timeout=15)
except subprocess.TimeoutExpired:
    proc.kill()
    stdout, stderr = proc.communicate()
    print("TIMEOUT after 15s")

stdout_text = stdout.decode("utf-8", errors="replace")
stderr_text = stderr.decode("utf-8", errors="replace")

print("\n=== STDOUT ===")
for line in stdout_text.split("\n"):
    if line.strip():
        try:
            resp = json.loads(line)
            if "result" in resp:
                if "tools" in resp["result"]:
                    tools = resp["result"]["tools"]
                    print(f"tools/list: {len(tools)} tools")
                    for t in tools:
                        print(f"  - {t['name']}")
                elif "serverInfo" in resp["result"]:
                    info = resp["result"]["serverInfo"]
                    print(f"initialize: server={info.get('name')}, v={info.get('version')}")
                    print(f"  protocol={resp['result'].get('protocolVersion')}")
                else:
                    r = resp["result"]
                    print(f"result id={resp['id']}: {json.dumps(r, ensure_ascii=False)[:200]}")
            elif "error" in resp:
                print(f"ERROR id={resp['id']}: {resp['error']}")
        except json.JSONDecodeError:
            print(f"RAW: {line[:200]}")

print("\n=== STDERR (last 300 chars) ===")
for line in stderr_text.split("\n")[-10:]:
    if line.strip():
        print(line[:200])

proc.kill() if proc.poll() is None else None
print("\nDone.")
