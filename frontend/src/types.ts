export type NodeType =
  | 'container'
  | 'agent'
  | 'concept'
  | 'finding'
  | 'theory'
  | 'synthesis'

export type SparkStatus = 'active' | 'resolved' | 'abandoned'
export type SparkType = 'open_question' | 'contradiction' | 'weak_edge' | 'isolated_node' | 'thin_domain'

export type EdgeType =
  | 'supports'
  | 'contradicts'
  | 'prerequisite_for'
  | 'extends'
  | 'example_of'
  | 'questions'
  | 'resolves'
  | 'inspired'
  | 'connects'
  | 'parent'

export interface NeoNode {
  id: string
  node_type: NodeType
  title: string
  summary: string | null
  confidence: number
  domain: string | null
  parent_id: string | null
  source_id: string | null
  spark_id: string | null
  created_at: string
  updated_at: string
  consolidation_version: number
  // Full content — only loaded on demand
  content?: string
}

export interface NeoEdge {
  id: string
  from_node_id: string
  to_node_id: string
  edge_type: EdgeType
  description: string
  weight: number
  created_at: string
}

export interface NeoSpark {
  id: string
  agent_id: string
  spark_type: SparkType
  description: string
  priority: number
  status: SparkStatus
  target_node_id: string | null
  created_at: string
  resolved_at: string | null
}

export interface GraphData {
  nodes: NeoNode[]
  edges: NeoEdge[]
  sparks: NeoSpark[]
  spark_node_counts: Record<string, number>
}

// react-force-graph-3d node shape
export interface GraphNode extends NeoNode {
  // injected by force graph
  x?: number
  y?: number
  z?: number
  vx?: number
  vy?: number
  vz?: number
  fx?: number
  fy?: number
  fz?: number
  __threeObj?: unknown
}

export interface GraphLink {
  id: string
  source: string | GraphNode
  target: string | GraphNode
  edge_type: EdgeType
  description: string
  weight: number
}
