"""Inspect CherryStudio MCP configurations from agents.db"""
import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

db_path = r"C:\Users\<YourUsername>\AppData\Roaming\CherryStudio\Data\agents.db"
db = sqlite3.connect(db_path)
cursor = db.cursor()

# Get all agents with MCP configs
cursor.execute("SELECT id, name, type, mcps FROM agents WHERE deleted_at IS NULL")
agents = cursor.fetchall()
print(f"=== Agents ({len(agents)} total) ===\n")

for agent_id, name, atype, mcps in agents:
    print(f"Agent: {name} (id={agent_id}, type={atype})")
    if mcps:
        try:
            mcp_list = json.loads(mcps)
            print(f"  MCPs: {json.dumps(mcp_list, indent=4, ensure_ascii=False)}")
        except:
            print(f"  MCPs (raw): {mcps[:500]}")
    else:
        print(f"  MCPs: None")
    print()

# Also check for a dedicated MCP servers table
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cursor.fetchall()]
if 'mcps' in tables or 'mcp_servers' in tables:
    mcp_table = 'mcps' if 'mcps' in tables else 'mcp_servers'
    print(f"\n=== {mcp_table} table ===")
    cursor.execute(f"SELECT * FROM [{mcp_table}]")
    cols = [d[0] for d in cursor.description]
    print(f"Columns: {cols}")
    for row in cursor.fetchall():
        for i, col in enumerate(cols):
            val = str(row[i])[:500] if row[i] else "NULL"
            print(f"  {col}: {val}")
        print("---")

# Check agent_skills for MCP tool bindings
cursor.execute("SELECT count(*) FROM agent_skills")
skill_count = cursor.fetchone()[0]
if skill_count > 0:
    print(f"\n=== agent_skills ({skill_count} rows) ===")
    cursor.execute("SELECT * FROM agent_skills LIMIT 10")
    cols = [d[0] for d in cursor.description]
    print(f"Columns: {cols}")
    for row in cursor.fetchall():
        for i, col in enumerate(cols):
            val = str(row[i])[:300] if row[i] else "NULL"
            print(f"  {col}: {val}")
        print("---")

# Check skills table
cursor.execute("SELECT count(*) FROM skills")
s_count = cursor.fetchone()[0]
if s_count > 0:
    print(f"\n=== skills ({s_count} rows) ===")
    cursor.execute("SELECT id, name, type FROM skills LIMIT 20")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} (type={row[2]})")

db.close()
