import { useState, useMemo, useCallback } from 'react'
import type { GraphNode, NodeType } from '../types'
import { nodeColor } from '../colors'

interface TreeItem {
  node: GraphNode
  children: TreeItem[]
  depth: number
}

/** Build a parent→children tree. Nodes whose parent isn't in the visible set become roots. */
function buildTree(nodes: GraphNode[]): TreeItem[] {
  const idSet = new Set(nodes.map(n => n.id))
  const childrenOf = new Map<string | null, GraphNode[]>()

  for (const node of nodes) {
    const parentKey = node.parent_id && idSet.has(node.parent_id) ? node.parent_id : null
    if (!childrenOf.has(parentKey)) childrenOf.set(parentKey, [])
    childrenOf.get(parentKey)!.push(node)
  }

  const TYPE_ORDER: NodeType[] = ['container', 'agent', 'concept', 'finding', 'theory', 'synthesis']
  const typeRank = (t: string) => {
    const i = TYPE_ORDER.indexOf(t as NodeType)
    return i === -1 ? 99 : i
  }

  function sortNodes(ns: GraphNode[]) {
    return [...ns].sort((a, b) =>
      typeRank(a.node_type) - typeRank(b.node_type) || a.title.localeCompare(b.title)
    )
  }

  function build(parentId: string | null, depth: number): TreeItem[] {
    const children = childrenOf.get(parentId) ?? []
    return sortNodes(children).map(node => ({
      node,
      children: build(node.id, depth + 1),
      depth,
    }))
  }

  return build(null, 0)
}

// ── Single row ────────────────────────────────────────────────────────────────

interface RowProps {
  item: TreeItem
  selectedId?: string | null
  onSelect: (node: GraphNode) => void
  collapsed: Set<string>
  onToggle: (id: string) => void
}

function TreeRow({ item, selectedId, onSelect, collapsed, onToggle }: RowProps) {
  const hasChildren = item.children.length > 0
  const isCollapsed = collapsed.has(item.node.id)
  const isSelected = item.node.id === selectedId
  const color = nodeColor(item.node.node_type as NodeType)

  return (
    <>
      <div
        className={`group flex items-center gap-1.5 py-[3px] pr-2 rounded cursor-pointer text-xs select-none
          transition-colors hover:bg-slate-700/50
          ${isSelected ? 'bg-blue-500/20 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
        style={{ paddingLeft: `${8 + item.depth * 14}px` }}
        onClick={() => onSelect(item.node)}
      >
        {/* Expand/collapse toggle */}
        {hasChildren ? (
          <button
            className="w-3 h-3 flex items-center justify-center text-slate-600 hover:text-slate-300 flex-shrink-0"
            onClick={e => { e.stopPropagation(); onToggle(item.node.id) }}
          >
            <span className="text-[9px]">{isCollapsed ? '▶' : '▼'}</span>
          </button>
        ) : (
          <span className="w-3 flex-shrink-0" />
        )}

        {/* Type colour dot */}
        <span
          className="w-2 h-2 rounded-full flex-shrink-0"
          style={{ background: color, boxShadow: `0 0 4px ${color}88` }}
        />

        {/* Title */}
        <span className="truncate leading-4">{item.node.title}</span>

        {/* Child count badge */}
        {hasChildren && (
          <span className="ml-auto pl-1 text-[10px] text-slate-600 flex-shrink-0">
            {item.children.length}
          </span>
        )}
      </div>

      {/* Children */}
      {!isCollapsed &&
        item.children.map(child => (
          <TreeRow
            key={child.node.id}
            item={child}
            selectedId={selectedId}
            onSelect={onSelect}
            collapsed={collapsed}
            onToggle={onToggle}
          />
        ))}
    </>
  )
}

// ── Domain group header ───────────────────────────────────────────────────────

interface GroupProps {
  label: string
  count: number
  collapsed: boolean
  onToggle: () => void
}

function GroupHeader({ label, count, collapsed, onToggle }: GroupProps) {
  return (
    <button
      className="w-full flex items-center gap-1.5 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider
        text-slate-500 hover:text-slate-300 transition-colors select-none"
      onClick={onToggle}
    >
      <span className="text-[8px]">{collapsed ? '▶' : '▼'}</span>
      <span className="truncate">{label || 'general'}</span>
      <span className="ml-auto text-slate-600 font-normal">{count}</span>
    </button>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  nodes: GraphNode[]
  selectedId?: string | null
  onSelect: (node: GraphNode) => void
}

export default function TreeView({ nodes, selectedId, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string | null>>(new Set())
  const [panelOpen, setPanelOpen] = useState(true)
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    if (!search.trim()) return nodes
    const q = search.toLowerCase()
    return nodes.filter(n => n.title.toLowerCase().includes(q) || n.summary?.toLowerCase().includes(q))
  }, [nodes, search])

  // When searching, show a flat list grouped by domain; otherwise show the full tree
  const useFlat = search.trim().length > 0

  // Group by domain
  const byDomain = useMemo<Map<string | null, GraphNode[]>>(() => {
    const map = new Map<string | null, GraphNode[]>()
    for (const node of filtered) {
      const key = node.domain ?? null
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(node)
    }
    return map
  }, [filtered])

  // Full tree (only used when not searching)
  const tree = useMemo(() => buildTree(filtered), [filtered])

  const toggleNode = useCallback((id: string) => {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const toggleGroup = useCallback((domain: string | null) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev)
      const key = domain ?? ''
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  // ── Collapsed panel (just a slim toggle strip) ─────────────────────────────
  if (!panelOpen) {
    return (
      <div className="w-7 flex-shrink-0 flex flex-col items-center pt-3 border-r border-slate-700/60 bg-slate-900/50">
        <button
          title="Show tree"
          className="text-slate-600 hover:text-slate-300 transition-colors"
          onClick={() => setPanelOpen(true)}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <div className="mt-4 text-[10px] text-slate-700 writing-mode-vertical select-none"
          style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
          {nodes.length} nodes
        </div>
      </div>
    )
  }

  // ── Full panel ─────────────────────────────────────────────────────────────
  return (
    <div className="w-56 flex-shrink-0 flex flex-col border-r border-slate-700/60 bg-slate-900/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-700/60 flex-shrink-0">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 flex-1">
          Nodes
        </span>
        <span className="text-[10px] text-slate-600">{nodes.length}</span>
        <button
          title="Collapse panel"
          className="text-slate-600 hover:text-slate-300 transition-colors"
          onClick={() => setPanelOpen(false)}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M9 3l-4 4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* Search */}
      <div className="px-2 py-1.5 border-b border-slate-700/40 flex-shrink-0">
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Filter nodes…"
          className="w-full bg-slate-800/60 text-xs text-slate-300 placeholder-slate-600
            border border-slate-700/50 rounded px-2 py-1 outline-none focus:border-slate-500
            transition-colors"
        />
      </div>

      {/* Tree / flat list */}
      <div className="flex-1 overflow-y-auto py-1 scrollbar-thin scrollbar-thumb-slate-700">
        {filtered.length === 0 && (
          <div className="px-3 py-6 text-xs text-slate-600 text-center">
            {search ? 'No matches' : 'No nodes'}
          </div>
        )}

        {useFlat
          // ── Flat grouped list when searching ──────────────────────────────
          ? Array.from(byDomain.entries())
              .sort(([a], [b]) => (a ?? '').localeCompare(b ?? ''))
              .map(([domain, domainNodes]) => {
                const key = domain ?? ''
                const isGroupCollapsed = collapsedGroups.has(key)
                const TYPE_ORDER: NodeType[] = ['container', 'agent', 'concept', 'finding', 'theory', 'synthesis']
                const typeRank = (t: string) => {
                  const i = TYPE_ORDER.indexOf(t as NodeType)
                  return i === -1 ? 99 : i
                }
                const sorted = [...domainNodes].sort((a, b) =>
                  typeRank(a.node_type) - typeRank(b.node_type) || a.title.localeCompare(b.title)
                )
                return (
                  <div key={key}>
                    {byDomain.size > 1 && (
                      <GroupHeader
                        label={domain ?? 'general'}
                        count={domainNodes.length}
                        collapsed={isGroupCollapsed}
                        onToggle={() => toggleGroup(domain)}
                      />
                    )}
                    {!isGroupCollapsed && sorted.map(node => {
                      const color = nodeColor(node.node_type as NodeType)
                      const isSelected = node.id === selectedId
                      return (
                        <div
                          key={node.id}
                          className={`flex items-center gap-1.5 py-[3px] px-2 rounded cursor-pointer text-xs select-none
                            transition-colors hover:bg-slate-700/50 mx-1
                            ${isSelected ? 'bg-blue-500/20 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
                          onClick={() => onSelect(node)}
                        >
                          <span className="w-2 h-2 rounded-full flex-shrink-0"
                            style={{ background: color, boxShadow: `0 0 4px ${color}88` }} />
                          <span className="truncate leading-4">{node.title}</span>
                        </div>
                      )
                    })}
                  </div>
                )
              })

          // ── Full tree when not searching ──────────────────────────────────
          : tree.map(item => (
              <TreeRow
                key={item.node.id}
                item={item}
                selectedId={selectedId}
                onSelect={onSelect}
                collapsed={collapsed}
                onToggle={toggleNode}
              />
            ))
        }
      </div>
    </div>
  )
}
