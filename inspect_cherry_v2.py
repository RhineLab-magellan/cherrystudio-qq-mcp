"""深入检查 CherryStudio agents.db 中所有表的完整 schema 和关键数据"""
import sqlite3
import json

DB_PATH = r"C:\Users\<YourUsername>\AppData\Roaming\CherryStudio\Data\agents.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. 列出所有表
print("=" * 80)
print("ALL TABLES:")
print("=" * 80)
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    print(f"  - {t}")

# 2. 每个表的 schema
print("\n" + "=" * 80)
print("TABLE SCHEMAS:")
print("=" * 80)
for t in tables:
    cur.execute(f"PRAGMA table_info('{t}')")
    cols = cur.fetchall()
    print(f"\n--- {t} ---")
    for c in cols:
        print(f"  {c['name']:30s} {c['type']:15s} pk={c['pk']} notnull={c['notnull']}")

# 3. 检查 skills 表 - 可能包含 MCP 服务器定义
print("\n" + "=" * 80)
print("SKILLS TABLE DATA:")
print("=" * 80)
try:
    cur.execute("SELECT * FROM skills LIMIT 20")
    rows = cur.fetchall()
    if rows:
        cols = rows[0].keys()
        print(f"Columns: {list(cols)}")
        for i, row in enumerate(rows):
            print(f"\n--- Skill #{i+1} ---")
            for col in cols:
                val = row[col]
                if isinstance(val, str) and len(val) > 200:
                    # Try to parse as JSON for readability
                    try:
                        parsed = json.loads(val)
                        print(f"  {col}: {json.dumps(parsed, indent=2, ensure_ascii=False)[:500]}")
                    except:
                        print(f"  {col}: {val[:200]}...")
                else:
                    print(f"  {col}: {val}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

# 4. 检查 agent_skills 表
print("\n" + "=" * 80)
print("AGENT_SKILLS TABLE DATA:")
print("=" * 80)
try:
    cur.execute("SELECT * FROM agent_skills LIMIT 20")
    rows = cur.fetchall()
    if rows:
        cols = rows[0].keys()
        print(f"Columns: {list(cols)}")
        for i, row in enumerate(rows):
            print(f"\n--- AgentSkill #{i+1} ---")
            for col in cols:
                val = row[col]
                if isinstance(val, str) and len(val) > 200:
                    try:
                        parsed = json.loads(val)
                        print(f"  {col}: {json.dumps(parsed, indent=2, ensure_ascii=False)[:500]}")
                    except:
                        print(f"  {col}: {val[:200]}...")
                else:
                    print(f"  {col}: {val}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

# 5. 检查 agents 表中 configuration 列的完整 JSON
print("\n" + "=" * 80)
print("AGENTS TABLE - CONFIGURATION COLUMN:")
print("=" * 80)
try:
    cur.execute("SELECT id, name, configuration FROM agents")
    rows = cur.fetchall()
    for row in rows:
        print(f"\n--- Agent: {row['name']} (id={row['id']}) ---")
        config_str = row['configuration']
        if config_str:
            try:
                config = json.loads(config_str)
                # Look for MCP-related keys
                print(f"  Top-level keys: {list(config.keys()) if isinstance(config, dict) else 'not a dict'}")
                # Print MCP-related parts
                for key in config:
                    if 'mcp' in key.lower() or 'server' in key.lower() or 'tool' in key.lower():
                        val = config[key]
                        print(f"  >>> {key}: {json.dumps(val, indent=2, ensure_ascii=False)[:1000]}")
                # Also print full config for first agent that has MCP refs
                if isinstance(config, dict) and any('mcp' in str(v).lower() for v in config.values()):
                    print(f"  FULL CONFIG (truncated):")
                    print(json.dumps(config, indent=2, ensure_ascii=False)[:3000])
            except:
                print(f"  Raw: {config_str[:500]}")
        else:
            print("  (no configuration)")
except Exception as e:
    print(f"  Error: {e}")

# 6. Check sessions table
print("\n" + "=" * 80)
print("SESSIONS TABLE (sample):")
print("=" * 80)
try:
    cur.execute("SELECT * FROM sessions LIMIT 5")
    rows = cur.fetchall()
    if rows:
        cols = rows[0].keys()
        print(f"Columns: {list(cols)}")
        for i, row in enumerate(rows):
            print(f"\n--- Session #{i+1} ---")
            for col in cols:
                val = row[col]
                if isinstance(val, str) and len(val) > 300:
                    try:
                        parsed = json.loads(val)
                        print(f"  {col}: {json.dumps(parsed, indent=2, ensure_ascii=False)[:500]}")
                    except:
                        print(f"  {col}: {val[:300]}...")
                else:
                    print(f"  {col}: {val}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

# 7. 搜索所有表中包含 "server.py" 或 "python" 或 "command" 的数据
print("\n" + "=" * 80)
print("SEARCHING ALL TABLES FOR MCP SERVER CONFIG:")
print("=" * 80)
search_terms = ['server.py', 'python', 'command', 'stdio', 'mcp_server', 'qq-mcp', 'CherryStudio\\qq']
for t in tables:
    cur.execute(f"PRAGMA table_info('{t}')")
    cols = [c['name'] for c in cur.fetchall()]
    for col in cols:
        for term in search_terms:
            try:
                cur.execute(f"SELECT * FROM {t} WHERE CAST(\"{col}\" AS TEXT) LIKE ? LIMIT 3", (f'%{term}%',))
                rows = cur.fetchall()
                if rows:
                    print(f"\n>>> FOUND '{term}' in {t}.{col} ({len(rows)} rows)")
                    for row in rows:
                        val = row[col]
                        if isinstance(val, str):
                            print(f"    Value (truncated): {val[:500]}")
                        else:
                            print(f"    Value: {val}")
            except Exception as e:
                pass

conn.close()
print("\n\nDone.")
