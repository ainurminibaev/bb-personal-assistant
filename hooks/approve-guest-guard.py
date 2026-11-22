"""Allow only the platform guest thread guard in Codex for the given folders, without the BB interface.

The installing owner runs it (setup.py --allow-guest-guard). It uses Codex's own app-server API, the same
calls the BB "Codex hooks" card makes, and touches no other hook. Usage: approve-guest-guard.py <folder>..."""
import json, subprocess, sys
GUARD_SUFFIX="/.bb/hooks/guest-thread-guard.py"
p=subprocess.Popen(["codex","app-server"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,bufsize=1)
n=0
def req(method,params):
    global n; n+=1; i=n
    p.stdin.write(json.dumps({"jsonrpc":"2.0","id":i,"method":method,"params":params})+"\n"); p.stdin.flush()
    while True:
        line=p.stdout.readline()
        if not line: raise SystemExit("app-server closed")
        try: r=json.loads(line)
        except ValueError: continue
        if r.get("id")==i:
            if "error" in r: raise SystemExit(f"{method}: {r['error'].get('message')}")
            return r["result"]
req("initialize",{"clientInfo":{"name":"platform-guest-guard","version":"1"}})
p.stdin.write(json.dumps({"jsonrpc":"2.0","method":"initialized"})+"\n"); p.stdin.flush()
for cwd in sys.argv[1:]:
    data=req("hooks/list",{"cwds":[cwd]})["data"]
    entry=next((d for d in data if d.get("cwd")==cwd),data[0] if data else {})
    hooks=[h for h in entry.get("hooks",[]) if h.get("command","").startswith("python3 ") and h.get("command","").endswith(GUARD_SUFFIX)]
    others=[h for h in entry.get("hooks",[]) if h not in hooks and h.get("trustStatus")!="trusted"]
    layers=req("config/read",{"cwd":cwd,"includeLayers":True})["layers"]
    user=next(l for l in layers if l["name"].get("type")=="user" and not l["name"].get("profile") and l["name"].get("file"))
    todo=[h for h in hooks if h["trustStatus"]!="trusted"]
    if todo:
        res=req("config/batchWrite",{"edits":[{"keyPath":"hooks.state."+json.dumps(h["key"])+".trusted_hash","value":h["currentHash"],"mergeStrategy":"replace"} for h in todo],
            "filePath":user["name"]["file"],"expectedVersion":user["version"],"reloadUserConfig":True})
        if res.get("status")!="ok": raise SystemExit(f"write status {res}")
    after=req("hooks/list",{"cwds":[cwd]})["data"]
    after=next((d for d in after if d.get("cwd")==cwd),after[0] if after else {})
    st=[(h["key"][-60:],h["trustStatus"]) for h in after.get("hooks",[]) if h.get("command","").endswith(GUARD_SUFFIX)]
    print(cwd,"guard:",st,"| other untrusted hooks left as is:",len(others))
p.stdin.close(); p.terminate()
