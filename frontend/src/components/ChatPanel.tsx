import { useState } from 'react'
import { sendChat } from '../api'

interface QA { role: 'human' | 'agent'; text: string }

export default function ChatPanel({ taskId, enabled }: { taskId: string | null; enabled: boolean }) {
  const [history, setHistory] = useState<QA[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const ask = async () => {
    const question = input.trim()
    if (!question || !taskId || busy) return
    setBusy(true)
    setHistory(h => [...h, { role: 'human', text: question }])
    setInput('')
    try {
      const { answer } = await sendChat(taskId, question)
      setHistory(h => [...h, { role: 'agent', text: answer }])
    } catch {
      setHistory(h => [...h, { role: 'agent', text: '问答服务异常,请稍后重试。' }])
    } finally { setBusy(false) }
  }

  return (
    <div className="chat-panel">
      <div className="panel-title">
        🎙️ 指挥员问答
        <span className={`llm-tag ${enabled ? 'on' : 'off'}`}>
          {enabled ? 'GLM 已接入' : '离线确定性'}
        </span>
      </div>
      <div className="chat-history">
        {history.length === 0 && (
          <div className="empty">
            {taskId
              ? '向智能参谋提问,例如:为什么选这 2 架灭火机?现在火势控制住了吗?水剂还剩多少?CO₂ 什么时候用?'
              : '开始任务后即可提问,回答基于黑板实时数据与内置知识库(规则/思路/论文)。'}
          </div>
        )}
        {history.map((qa, i) => (
          <div key={i} className={qa.role === 'human' ? 'bubble human' : 'bubble agent'}>
            {qa.role === 'human' ? '🧑‍✈️ ' : '🤖 '}{qa.text}
          </div>
        ))}
        {busy && <div className="bubble agent typing">🤖 思考中…</div>}
      </div>
      <div className="chat-input">
        <input
          value={input}
          placeholder={taskId ? '输入问题,回车发送…' : '请先开始任务'}
          disabled={!taskId || busy}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ask()}
        />
        <button className="primary" disabled={!taskId || busy || !input.trim()} onClick={ask}>发送</button>
      </div>
    </div>
  )
}
