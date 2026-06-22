"""读取 CherryStudio Local Storage LevelDB 中的所有键值对"""
import subprocess
import sys
import os

# Try using plyvel if available, otherwise use a raw binary approach
# 将路径替换为你的 CherryStudio LevelDB 目录
LDB_DIR = r"C:\Users\<YourUsername>\AppData\Roaming\CherryStudio\Local Storage\leveldb"

# Method 1: Try to read raw .ldb and .log files as binary and extract strings
print("Reading LevelDB files as binary, searching for MCP-related content...")
print("=" * 80)

mcp_keywords = [b'mcp', b'MCP', b'server.py', b'python', b'command', b'args', b'stdio', 
                b'qq-mcp', b'bridge', b'cherrystudio']

# Read all .ldb and .log files
for filename in sorted(os.listdir(LDB_DIR)):
    if not (filename.endswith('.ldb') or filename.endswith('.log')):
        continue
    filepath = os.path.join(LDB_DIR, filename)
    print(f"\n--- {filename} ({os.path.getsize(filepath)} bytes) ---")
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # Search for MCP-related strings
    for kw in mcp_keywords:
        idx = 0
        found_positions = []
        while True:
            pos = data.find(kw, idx)
            if pos == -1:
                break
            found_positions.append(pos)
            idx = pos + 1
        
        if found_positions:
            print(f"\n  >>> Found '{kw.decode('utf-8', errors='replace')}' at {len(found_positions)} positions")
            # Show context around first few matches
            for pos in found_positions[:5]:
                start = max(0, pos - 80)
                end = min(len(data), pos + len(kw) + 200)
                context = data[start:end]
                # Try to decode as utf-8, replacing errors
                text = context.decode('utf-8', errors='replace')
                # Clean non-printable chars
                text_clean = ''.join(c if c.isprintable() or c in ' \n\t' else '.' for c in text)
                print(f"    @{pos}: ...{text_clean}...")

# Method 2: Extract ALL readable strings from LevelDB files
print("\n\n" + "=" * 80)
print("ALL READABLE STRINGS (min length 20) from .ldb and .log files:")
print("=" * 80)

import re

for filename in sorted(os.listdir(LDB_DIR)):
    if not (filename.endswith('.ldb') or filename.endswith('.log')):
        continue
    filepath = os.path.join(LDB_DIR, filename)
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    # Extract UTF-8 strings (minimum 20 chars)
    strings = re.findall(b'[\x20-\x7e]{20,}', data)
    
    # Also try to find UTF-16 strings (common in LevelDB from Electron)
    # UTF-16LE strings have pattern: char\x00char\x00...
    utf16_pattern = re.findall(b'(?:[\x20-\x7e]\x00){10,}', data)
    
    mcp_related = []
    for s in strings:
        text = s.decode('ascii', errors='replace')
        if any(kw.decode().lower() in text.lower() for kw in [b'mcp', b'server.py', b'python', b'stdio', b'command', b'qq-mcp', b'bridge']):
            mcp_related.append(text)
    
    for s in utf16_pattern:
        text = s.decode('utf-16-le', errors='replace')
        if any(kw.decode().lower() in text.lower() for kw in [b'mcp', b'server.py', b'python', b'stdio', b'command', b'qq-mcp', b'bridge']):
            mcp_related.append(f"[UTF-16] {text}")
    
    if mcp_related:
        print(f"\n--- {filename} ---")
        for s in mcp_related[:30]:
            print(f"  {s[:300]}")
