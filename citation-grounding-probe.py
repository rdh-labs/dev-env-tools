import json, pathlib, re
T = pathlib.Path.home()/".claude/projects/-home-ichardart-dev/231ba059-e8ad-4086-8168-4ffcade56040.jsonl"
MEMDIR = pathlib.Path.home()/".claude/projects/-home-ichardart/memory"
slugs = {p.stem for p in MEMDIR.glob("*.md")} - {"MEMORY"}
READCMD = re.compile(r"\b(cat|sed|head|tail|less|awk|grep|rg)\b")
first_named, first_read = {}, {}
for i, line in enumerate(T.open()):
    try: rec = json.loads(line)
    except Exception: continue
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str): content = [{"type":"text","text":content}]
    if not isinstance(content, list): continue
    for blk in content:
        if not isinstance(blk, dict): continue
        if blk.get("type")=="text" and rec.get("type")=="assistant":
            for s in slugs:
                if s in blk.get("text","") and s not in first_named: first_named[s]=i
        elif blk.get("type")=="tool_use":
            name, inp = blk.get("name",""), blk.get("input",{}) or {}
            fp, cmd = str(inp.get("file_path","")), str(inp.get("command",""))
            for s in slugs:
                hit = (name=="Read" and s in fp) or \
                      (name=="Bash" and s in cmd and READCMD.search(cmd) and ">" not in cmd)
                if hit and s not in first_read: first_read[s]=i
named=set(first_named)
never=sorted(s for s in named if s not in first_read)
after=sorted(s for s in named if s in first_read and first_read[s]>first_named[s])
before=sorted(s for s in named if s in first_read and first_read[s]<first_named[s])
print(f"denominator — distinct memory slugs I NAMED in prose: {len(named)}")
print(f"  READ before first naming (grounded)               : {len(before)}  {before}")
print(f"  READ only after first naming (gloss, later checked): {len(after)}  {after}")
print(f"  NEVER read this session (pure gloss)              : {len(never)}")
print(f"  ungrounded-at-time-of-claim = {len(after)+len(never)}/{len(named)} = {100*(len(after)+len(never))/len(named):.0f}%")
print()
print("PURE GLOSS:", never)
