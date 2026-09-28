import request from '../utils/request'

// SSE 事件类型（与 react_engine.py 中 sse_events.make_* 生成的一致）
export type SseEventType =
  | 'ready'      // { run_id }
  | 'retrieval'  // { hit_count, content_preview, stats? }
  | 'citations'  // { citations: [{ index, knowledge_id, file_name, space_id, space_name }] }
  | 'thinking'   // { content, tool_calls? }
  | 'tool_call'  // { name, args, step_no }
  | 'tool_result'// { name, result, step_no }
  | 'answer'     // { content }  最终回答（完整字符串，不是增量 token）
  | 'answer_delta'
  | 'done'       // { run_id, steps, answer_length, conversation_id? }
  | 'error'      // { message, detail? }

export interface Citation {
  index: number
  knowledge_id: number
  file_name: string
  space_id: number | null
  space_name?: string
  snippet?: string
}

/** RAG 上下文压缩 / Token 节省（后端 service/rag/rag_stats.py::build_savings） */
export interface RagSavings {
  source_doc_chars: number
  recall_chunks: number
  context_chars: number
  saved_ratio: number
  est_tokens_full: number
  est_tokens_context: number
  est_tokens_saved: number
}

export interface SseEvent {
  type: SseEventType
  run_id?: number
  hit_count?: number
  content_preview?: string
  stats?: RagSavings
  citations?: Citation[]
  content?: string
  tool_calls?: any[]
  name?: string
  args?: Record<string, any>
  step_no?: number
  result?: string
  steps?: number
  answer_length?: number
  tokens?: number
  conversation_id?: number
  message?: string
  detail?: string
}

export interface SendStreamOptions {
  agentId: number
  conversationId?: number | null
  message: string
  /** 先经 uploadAttachment 上传后得到的附件 ID */
  attachmentIds?: string[]
  onEvent: (evt: SseEvent) => void
  signal?: AbortSignal
}

/**
 * 流式对话（SSE，fetch + ReadableStream，按 SSE 规范逐行解析）
 * 后端每个事件块形如：
 *   event: answer
 *   data: {"content": "完整回答"}
 *
 *   data: {"content":"心跳或仅 data 的事件"}
 */
export async function sendStream(opts: SendStreamOptions): Promise<void> {
  const { agentId, conversationId, message, attachmentIds, onEvent, signal } = opts
  const token = localStorage.getItem('token') || ''

  const resp = await fetch(`/api/chat/${agentId}/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId ?? undefined,
      attachment_ids: attachmentIds?.length ? attachmentIds : undefined,
    }),
    signal,
  })

  if (!resp.ok) {
    let errMsg = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      errMsg = j?.detail || errMsg
    } catch { /* ignore */ }
    onEvent({ type: 'error', message: errMsg })
    return
  }

  const reader = resp.body!.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''          // 跨 chunk 累积
  let currentEventName: string = '' // event: xxx 决定下一个 data 的事件名

  // 单个事件块（多个 data: 行累积，直到遇到空行触发一次回调）
  let dataPayloads: string[] = []

  const flushEvent = () => {
    if (dataPayloads.length === 0 && !currentEventName) return
    // SSE 允许多个 data 行，用 \n 拼接
    const raw = dataPayloads.join('\n')
    dataPayloads = []
    let payload: any = {}
    if (raw) {
      try {
        payload = JSON.parse(raw)
      } catch {
        // 非 JSON 的 data 直接作为 content
        payload = { content: raw }
      }
    }
    // 映射 event name -> 事件
    const type: SseEventType = (currentEventName || 'answer') as SseEventType
    const evt: SseEvent = { type, ...payload }
    // 若未命名事件且无法判断，至少给一个 done 的兜底
    if (!currentEventName && !raw) {
      currentEventName = ''
      return
    }
    onEvent(evt)
    currentEventName = ''
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 逐行切分（兼容 \n / \r\n / \r）
    let idx: number
    // 以 \n 为主分隔
    while ((idx = buffer.indexOf('\n')) !== -1) {
      let line = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 1)
      // 去掉尾部 \r
      if (line.endsWith('\r')) line = line.slice(0, -1)

      if (line === '') {
        // 空行 → 触发一个 SSE 事件的分发
        flushEvent()
        continue
      }
      if (line.startsWith(':')) {
        // SSE 注释行，跳过
        continue
      }
      if (line.startsWith('event:')) {
        currentEventName = line.slice(6).trim()
        continue
      }
      if (line.startsWith('data:')) {
        const data = line.slice(5).trimStart()
        dataPayloads.push(data)
        continue
      }
      // 其他忽略
    }
  }

  // 流结束，可能还有残留事件（最后一块没以空行结尾）
  if (dataPayloads.length > 0 || currentEventName) {
    flushEvent()
  }
}

/** 高风险工具（submit/approve/reject 等）的 tool_result.result 是这个 JSON 形状
 *  （见 service/tools/langchain_adapter.py 的 high_risk 拦截逻辑），不是真的执行结果。 */
export interface ToolConfirmationRequired {
  status: 'confirmation_required'
  confirmation_token: string
  tool_name: string
  tool_args: Record<string, any>
  expires_at: string
  message: string
}

export function parseConfirmationRequired(result: string | undefined): ToolConfirmationRequired | null {
  if (!result) return null
  try {
    const parsed = JSON.parse(result)
    return parsed?.status === 'confirmation_required' ? (parsed as ToolConfirmationRequired) : null
  } catch {
    return null
  }
}

/** 用户点击确认：真正执行这次高风险工具调用（第五轮审计 P0-2） */
export async function confirmToolCall(token: string): Promise<{ tool_name: string; result: string }> {
  const { data } = await request.post(`/chat/tool-confirmations/${token}/confirm`)
  return data
}

/** 用户点击取消：这次高风险工具调用不会被执行 */
export async function rejectToolCall(token: string): Promise<void> {
  await request.post(`/chat/tool-confirmations/${token}/reject`)
}
