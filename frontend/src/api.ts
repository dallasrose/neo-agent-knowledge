import type { GraphData, NeoNode, NeoEdge } from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export async function fetchGraph(limit = 100000): Promise<GraphData> {
  return get<GraphData>(`/graph?limit=${limit}`)
}

export async function fetchNode(id: string): Promise<{ node: NeoNode; edges: NeoEdge[] }> {
  return get(`/nodes/${id}`)
}

export async function searchNodes(query: string): Promise<{ nodes: NeoNode[] }> {
  const res = await fetch(`${BASE}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: 20, hop_depth: 1, min_weight: 0.0, token_budget: 10000 }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function deleteNode(id: string): Promise<{ deleted: boolean }> {
  const res = await fetch(`${BASE}/nodes/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function moveNode(id: string, parentId: string | null): Promise<NeoNode> {
  const res = await fetch(`${BASE}/nodes/${id}/parent`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parent_id: parentId }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
