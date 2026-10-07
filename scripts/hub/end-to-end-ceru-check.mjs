/**
 * 端到端联调：用插件改造后的协议（GET→PUT→GET，连 /ceru）打真实 m2-sync-hub。
 * 只验证澜音适配器（本插件协议）一侧；栖弦端只读看结构，不写入（避免格式误判污染数据）。
 */
const BASE = 'http://127.0.0.1:8000'
const CERU = BASE + '/ceru/sync-v1.json'
const CYS = BASE + '/CyShineMusic/sync-v1.json'
const AUTH = 'Basic ' + Buffer.from('admin:admin123').toString('base64')

async function http(method, url, body) {
  const headers = { Authorization: AUTH, 'Content-Type': 'application/json' }
  const resp = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined })
  let json = null
  try { json = await resp.json() } catch { json = await resp.text() }
  return { status: resp.status, json }
}

console.log('=== 插件侧 /ceru 协议联调 ===')

// 1) 首拉 GET（建立/刷新交付基线）
const g1 = await http('GET', CERU)
console.log('澜音 GET /ceru (首拉) →', g1.status, '| playlists:', (g1.json?.playlists || []).length, '| cleanup:', (g1.json?.cleanup || []).length)

// 2) PUT 提交本地视图（parse_ceru 线格式）
const ceruPut = await http('PUT', CERU, {
  playlists: [
    { id: 'ceru:local:1', name: 'Yes', tracks: [
      { source: 'tx', songId: 'aaa', title: 'A歌', singer: 'A' },
      { source: 'tx', songId: 'bbb', title: 'B歌', singer: 'B' },
    ] },
    { id: 'ceru:local:2', name: '新歌单', tracks: [{ source: 'wy', songId: '999', title: '本地新增', singer: 'Y' }] },
  ],
})
console.log('澜音 PUT /ceru →', ceruPut.status, JSON.stringify(ceruPut.json))

// 3) GET 合并结果
const g2 = await http('GET', CERU)
console.log('澜音 GET /ceru (合并后) →', g2.status)
console.log('  歌单:', (g2.json?.playlists || []).map((p) => `${p.name}(${p.tracks.length}首)`).join(', '))
const yes1 = (g2.json?.playlists || []).find((p) => p.name === 'Yes')
console.log('  Yes 歌曲:', (yes1?.tracks || []).map((t) => `${t.source}:${t.songId}`).join(', '))

// 4) 删除传播：澜音删掉 bbb → 提交 → 重复确认直到引擎采纳（I2'/D21 安全阀允许挂起，需后续轮次确认）
console.log('=== 删除传播 ===')
await http('GET', CERU) // 推进 base_served（删除判定只以交付基线为准）
for (let round = 1; round <= 6; round++) {
  const dput = await http('PUT', CERU, {
    playlists: [{ id: 'ceru:local:1', name: 'Yes', tracks: [{ source: 'tx', songId: 'aaa', title: 'A歌', singer: 'A' }] }],
  })
  const g3 = await http('GET', CERU)
  const yes3 = (g3.json?.playlists || []).find((p) => p.name === 'Yes')
  const ids = (yes3?.tracks || []).map((t) => `${t.source}:${t.songId}`).join(', ')
  console.log(`轮次${round} PUT→${dput.status} deferred=${dput.json && dput.json.deferred} | Yes: [${ids}]`)
  if (!(yes3 && yes3.tracks).some((t) => t.songId === 'bbb')) {
    console.log('✓ 删除最终传播，bbb 已从合并结果消失')
    break
  }
}

// 5) 栖弦端只读看结构（不写，避免格式误判）
const cysGet = await http('GET', CYS)
console.log('=== 栖弦端只读 ===')
console.log('栖弦 GET /CyShineMusic →', cysGet.status)
console.log('  顶层键:', cysGet.json ? Object.keys(cysGet.json) : '(非 JSON)')
