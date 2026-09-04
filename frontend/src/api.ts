import type { AgentMessage, Snapshot } from './types'

export async function createMission(scenario: string): Promise<{ task_id: string; snapshot: Snapshot }> {
  const res = await fetch('/api/missions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario }),
  })
  if (!res.ok) throw new Error(`createMission ${res.status}`)
  return res.json()
}

export async function getSnapshot(taskId: string): Promise<Snapshot> {
  const res = await fetch(`/api/missions/${taskId}`)
  if (!res.ok) throw new Error(`getSnapshot ${res.status}`)
  return res.json()
}

export async function postApproval(
  taskId: string, decision: 'approve' | 'reject' | 'adjust', feedback = '', peopleStatus?: string | null,
): Promise<{ task_id: string; snapshot: Snapshot }> {
  const res = await fetch(`/api/missions/${taskId}/approval`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, feedback, people_status: peopleStatus ?? null }),
  })
  if (!res.ok) throw new Error(`postApproval ${res.status}: ${await res.text()}`)
  return res.json()
}

export function subscribe(
  taskId: string,
  onMessage: (m: AgentMessage) => void,
  onDone: () => void,
): () => void {
  const es = new EventSource(`/api/missions/${taskId}/events?last_seq=0`)
  es.addEventListener('agent_message', (e) => onMessage(JSON.parse((e as MessageEvent).data)))
  es.addEventListener('done', () => { es.close(); onDone() })
  es.onerror = () => { /* 断流由上层快照轮询兜底 */ }
  return () => es.close()
}
