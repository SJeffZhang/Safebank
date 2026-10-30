import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
errs = [str(e.get("detail"))[:400] for e in d.get("events", []) if e.get("kind") == "error"]
dec = [e["detail"] for e in d.get("events", []) if e.get("kind") == "result" and isinstance(e.get("detail"), dict) and "decision" in e["detail"]]
print((" | ".join(errs) + (" DEC=" + json.dumps(dec[-1], ensure_ascii=False)[:300] if dec else "")).replace("\n", " "))
