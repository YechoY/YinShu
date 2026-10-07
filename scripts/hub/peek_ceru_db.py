import sqlite3, os, json

db = os.path.expandvars(r"%APPDATA%\ceru-music\playlists.db")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = conn.cursor()

cur.execute("SELECT id, name, source FROM playlists")
pls = cur.fetchall()
print("=== 本地歌单 ===")
for pid, name, src in pls:
    cur.execute("SELECT COUNT(*) FROM playlist_songs WHERE playlist_id=?", (pid,))
    n = cur.fetchone()[0]
    print(f"  [{pid}] {name!r} source={src} 歌曲={n}")

# Yes 歌单
yes = [p for p in pls if p[1] == "Yes"]
if yes:
    pid = yes[0][0]
    cur.execute("SELECT songmid, song_key, name, position, substr(data,1,200) FROM playlist_songs WHERE playlist_id=? ORDER BY position", (pid,))
    rows = cur.fetchall()
    print(f"\n=== Yes 本地歌曲（{len(rows)} 首，按 position） ===")
    target = None
    for r in rows:
        songmid, skey, name, pos, data = r
        mark = ""
        if songmid == "002xIrgo1GyEaG" or (skey and "002xIrgo1GyEaG" in str(skey)):
            mark = "  <<< 目标歌在这里"
            target = r
        if pos < 8 or mark:
            print(f"  pos={pos} songmid={songmid!r} key={skey!r} name={name!r}{mark}")
    print(f"\n目标歌 tx:002xIrgo1GyEaG 在本地 Yes: {'有' if target else '没有'}")
    if target:
        print("目标歌完整 data:", target[5][:500])
    # 统计 data 里 source 分布（看是否白名单）
    from collections import Counter
    srcs = Counter()
    for r in rows:
        try:
            d = json.loads(r[5]) if r[5] else {}
            srcs[d.get("source")] += 1
        except Exception:
            srcs["?"] += 1
    print("本地 Yes source 分布:", dict(srcs))
conn.close()
