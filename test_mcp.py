"""test_mcp.py — 模拟 MCP 客户端走一遍 stdio 握手 + tools/list + tools/call。"""
import json
import subprocess
import sys

proc = subprocess.Popen(
    [sys.executable, "mcp_server.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8")


def send(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def recv():
    line = proc.stdout.readline()
    return json.loads(line) if line.strip() else None


# 1) initialize
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
r = recv()
assert r["id"] == 1 and "tools" in r["result"]["capabilities"], r
print("initialize OK ->", r["result"]["serverInfo"])

# 2) initialized 通知（无响应）
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 3) tools/list
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
r = recv()
names = [t["name"] for t in r["result"]["tools"]]
assert "eyegive_vision" in names and "eyegive_health" in names, names
print("tools/list OK ->", names)

# 4) tools/call: health
send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "eyegive_health", "arguments": {}}})
r = recv()
out = json.loads(r["result"]["content"][0]["text"])
assert isinstance(out["vision_available"], bool) and "channels" in out, out
print("tools/call health OK -> vision_available:", out["vision_available"], "| watermark:", out.get("watermark"))

# 5) tools/call: vision(document 解析 txt，走零依赖链路，应成功)
send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
      "params": {"name": "eyegive_vision",
                 "arguments": {"task_type": "document", "source": "testdata/note.txt"}}})
r = recv()
out = json.loads(r["result"]["content"][0]["text"])
assert out["code"] == 0 and "第一条" in out["result"], out
print("tools/call vision(document) OK ->", out["result"][:20], "...")

# 6) tools/call: vision(reasoning 真实调用：有通道应 code=0，无通道应 code=1)
send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
      "params": {"name": "eyegive_vision",
                 "arguments": {"task_type": "reasoning", "source": "testdata/tiny.png",
                                "task": "描述这张图", "no_cache": True}}})
r = recv()
out = json.loads(r["result"]["content"][0]["text"])
if out["code"] == 0:
    print("tools/call vision(reasoning) 真实识图成功 ->", out["result"][:40], "...")
else:
    print("tools/call vision(reasoning) 预期降级 ->", out["error"][:40])

proc.stdin.close()
proc.wait(timeout=10)
print("MCP stdio 全链路测试通过")
