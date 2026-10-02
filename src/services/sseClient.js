/**
 * SSE 流式客户端。
 *
 * 用 fetch + ReadableStream 而不是 EventSource：EventSource 只支持 GET，
 * 而对话请求必须 POST 一整段 messages 历史。
 *
 * 解析要兼容三个坑：
 *  1. TCP 分片 —— 一次 read 拿到的可能只是半个事件块，必须自己缓冲；
 *  2. 事件之间用 \n\n 分隔，字段行是 `event: xxx` / `data: {...}`；
 *  3. 中文按 UTF-8 多字节传输，**不能**用 TextDecoder 逐块默认流式解码 ——
 *     一个汉字会被拆到两个块里，必须用 stream:true 让解码器自己处理。
 */
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

/**
 * @param {object} body      POST 请求体
 * @param {(type:string, data:object)=>void} onEvent  每收到一个事件回调一次
 * @param {AbortSignal} [signal] 外部取消
 */
export async function streamChat(body, onEvent, signal) {
  const url = `${API_BASE}/ai/chat/stream`
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(body),
    signal
  })

  if (!res.ok) {
    let msg = `流式请求失败 (HTTP ${res.status})`
    try {
      const j = await res.json()
      msg = j?.message || msg
    } catch { /* 非 JSON 响应，保留默认文案 */ }
    throw new Error(msg)
  }
  if (!res.body) {
    throw new Error('当前环境不支持流式响应')
  }

  const reader = res.body.getReader()
  // stream:true —— 关键，否则中文会被按字节切断成乱码
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 一个事件块以空行结束
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() || ''

    for (const block of blocks) {
      if (!block.trim()) continue
      let type = 'message'
      const dataLines = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) type = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (!dataLines.length) continue
      const raw = dataLines.join('\n')
      let data = {}
      try { data = JSON.parse(raw) } catch { data = { text: raw } }
      onEvent(type, data)
    }
  }
}
