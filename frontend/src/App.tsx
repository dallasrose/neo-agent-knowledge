import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { fetchGraph, searchNodes } from './api'
import type { GraphNode, GraphLink, NeoNode, NeoSpark, NodeType } from './types'
import Graph, { type GraphHandle } from './components/Graph'
import NodePanel from './components/NodePanel'
import SparkPanel from './components/SparkPanel'
import FilterBar from './components/FilterBar'
import TreeView from './components/TreeView'

const ALL_TYPES = new Set<NodeType>(['container', 'agent', 'concept', 'finding', 'theory', 'synthesis'])

export default function App() {
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [links, setLinks] = useState<GraphLink[]>([])
  const [sparks, setSparks] = useState<NeoSpark[]>([])
  const [sparkNodeCounts, setSparkNodeCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [selectedNode, setSelectedNode] = useState<NeoNode | null>(null)
  const [selectedSpark, setSelectedSpark] = useState<NeoSpark | null>(null)
  const [activeTypes, setActiveTypes] = useState<Set<NodeType>>(new Set(ALL_TYPES))
  const [search, setSearch] = useState('')
  const [highlightIds, setHighlightIds] = useState<Set<string>>(new Set())
  const [searchActive, setSearchActive] = useState(false)

  const nodeById = useRef<Map<string, GraphNode>>(new Map())
  const graphRef = useRef<GraphHandle>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchGraph()
      const gNodes = data.nodes as GraphNode[]
      nodeById.current = new Map(gNodes.map(n => [n.id, n]))
      setNodes(gNodes)
      const explicitLinks = data.edges.map(e => ({
        id: e.id,
        source: e.from_node_id,
        target: e.to_node_id,
        edge_type: e.edge_type,
        description: e.description,
        weight: e.weight,
      }))
      const parentLinks = gNodes
        .filter(n => n.parent_id)
        .map(n => ({
          id: `parent-${n.id}`,
          source: n.parent_id as string,
          target: n.id,
          edge_type: 'parent' as const,
          description: '',
          weight: 1,
        }))
      setLinks([...explicitLinks, ...parentLinks])
      setSparks(data.sparks)
      setSparkNodeCounts(data.spark_node_counts ?? {})
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const visibleNodes = useMemo(() => {
    const byType = nodes.filter(n => activeTypes.has(n.node_type as NodeType))
    if (!searchActive || highlightIds.size === 0) return byType
    return byType.filter(n => highlightIds.has(n.id))
  }, [nodes, activeTypes, searchActive, highlightIds])

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map(n => n.id)), [visibleNodes])

  const visibleLinks = useMemo(() =>
    links.filter(l => {
      const src = typeof l.source === 'string' ? l.source : (l.source as GraphNode).id
      const tgt = typeof l.target === 'string' ? l.target : (l.target as GraphNode).id
      return visibleNodeIds.has(src) && visibleNodeIds.has(tgt)
    }),
  [links, visibleNodeIds])

  const visibleSparks = useMemo(() =>
    sparks.filter(s => !s.target_node_id || visibleNodeIds.has(s.target_node_id)),
  [sparks, visibleNodeIds])

  const handleToggleType = useCallback((type: NodeType) => {
    setActiveTypes(prev => {
      const next = new Set(prev)
      if (next.has(type)) {
        if (next.size > 1) next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }, [])

  const handleNodeClick = useCallback((node: GraphNode) => {
    const n = node as GraphNode & { _isSpark?: boolean; _sparkData?: NeoSpark }
    if (n._isSpark && n._sparkData) {
      setSelectedSpark(n._sparkData)
      setSelectedNode(null)
      return
    }
    setSelectedNode(node)
    setSelectedSpark(null)
  }, [])

  const handleNavigate = useCallback((id: string) => {
    const n = nodeById.current.get(id)
    if (n) setSelectedNode(n)
  }, [])

  const handleNodeDeleted = useCallback((id: string) => {
    setNodes(prev => prev.filter(n => n.id !== id))
    setLinks(prev => prev.filter(l => {
      const src = typeof l.source === 'string' ? l.source : (l.source as GraphNode).id
      const tgt = typeof l.target === 'string' ? l.target : (l.target as GraphNode).id
      return src !== id && tgt !== id
    }))
    nodeById.current.delete(id)
    setSelectedNode(null)
  }, [])

  const handleNodeMoved = useCallback(() => {
    load()
    setSelectedNode(null)
  }, [load])

  const handleTreeSelect = useCallback((node: GraphNode) => {
    setSelectedNode(node)
    graphRef.current?.focusNode(node.id)
  }, [])

  const handleSearchSubmit = useCallback(async () => {
    if (!search.trim()) {
      setHighlightIds(new Set())
      setSearchActive(false)
      return
    }
    try {
      const result = await searchNodes(search)
      const ids = new Set(result.nodes.map((n: NeoNode) => n.id))
      setHighlightIds(ids)
      setSearchActive(true)
    } catch (e) {
      console.error(e)
    }
  }, [search])

  const handleSearchChange = useCallback((q: string) => {
    setSearch(q)
    if (!q) {
      setHighlightIds(new Set())
      setSearchActive(false)
    }
  }, [])

  const activeSparks = sparks.filter(s => s.status === 'active')

  return (
    <div className="flex flex-col h-screen bg-[#050a14] text-slate-200 overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2 bg-slate-900 border-b border-slate-700/60 flex-shrink-0">
        <span className="text-sm font-semibold tracking-wide text-slate-200 select-none">
          <span className="text-blue-400">Neo</span>
          <span className="text-slate-500 ml-1 font-normal">Knowledge Graph</span>
        </span>
        {activeSparks.length > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30">
            {activeSparks.length} active sparks
          </span>
        )}
      </div>

      {/* Filter bar */}
      <FilterBar
        search={search}
        onSearch={handleSearchChange}
        onSearchSubmit={handleSearchSubmit}
        activeTypes={activeTypes}
        onToggleType={handleToggleType}
        nodeCount={visibleNodes.length}
        edgeCount={visibleLinks.length}
        sparkCount={visibleSparks.length}
        onRefresh={load}
        loading={loading}
      />

      {/* Main area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left tree panel */}
        <TreeView
          nodes={visibleNodes}
          selectedId={selectedNode?.id}
          onSelect={handleTreeSelect}
        />

        {/* Graph */}
        <div className="flex-1 relative overflow-hidden">
          {loading && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <div className="text-slate-500 text-sm">Loading graph…</div>
            </div>
          )}
          {error && (
            <div className="absolute inset-0 flex items-center justify-center z-10">
              <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 max-w-sm">
                <p className="text-red-300 text-sm">{error}</p>
                <button className="mt-2 text-xs text-red-400 underline" onClick={load}>Retry</button>
              </div>
            </div>
          )}
          {!error && (
            <Graph
              ref={graphRef}
              nodes={visibleNodes}
              links={visibleLinks}
              sparks={visibleSparks}
              sparkNodeCounts={sparkNodeCounts}
              highlightIds={searchActive ? highlightIds : undefined}
              selectedId={selectedNode?.id}
              onNodeClick={handleNodeClick}
              onBackgroundClick={() => { setSelectedNode(null); setSelectedSpark(null) }}
            />
          )}

          {/* Legend */}
          <div className="absolute bottom-4 left-4 bg-slate-900/80 backdrop-blur rounded-lg px-3 py-2 text-xs space-y-1 border border-slate-700/40 select-none">
            {[
              { color: '#475569', label: 'container' },
              { color: '#d946ef', label: 'agent' },
              { color: '#93c5fd', label: 'concept' },
              { color: '#2563eb', label: 'finding' },
              { color: '#ea580c', label: 'theory' },
              { color: '#00e5cc', label: 'synthesis' },
              { color: '#fbbf24', label: 'spark ✦' },
            ].map(({ color, label }) => (
              <div key={label} className="flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: color }} />
                <span className="text-slate-400">{label}</span>
              </div>
            ))}
          </div>

          {nodes.length === 0 && !loading && !error && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-slate-600">
              <p className="text-lg">No nodes yet</p>
              <p className="text-sm">Store some knowledge in Neo to begin</p>
            </div>
          )}
        </div>

        {/* Side panel */}
        {(selectedNode || selectedSpark) && (
          <div className="w-80 flex-shrink-0 flex flex-col overflow-hidden border-l border-slate-700">
            {selectedNode && (
              <NodePanel
                node={selectedNode}
                allNodes={nodes}
                onClose={() => setSelectedNode(null)}
                onNavigate={handleNavigate}
                onDeleted={handleNodeDeleted}
                onMoved={handleNodeMoved}
              />
            )}
            {selectedSpark && (
              <SparkPanel
                spark={selectedSpark}
                nodeById={nodeById.current}
                onClose={() => setSelectedSpark(null)}
                onNavigate={(id) => {
                  setSelectedSpark(null)
                  handleNavigate(id)
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
