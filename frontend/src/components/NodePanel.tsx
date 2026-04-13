import { useEffect, useRef, useState } from 'react'
import { fetchNode, deleteNode, moveNode } from '../api'
import { nodeColor, EDGE_COLORS } from '../colors'
import type { NeoNode, NeoEdge, NodeType, EdgeType, GraphNode } from '../types'

interface Props {
  node: NeoNode
  allNodes: GraphNode[]
  onClose: () => void
  onNavigate: (id: string) => void
  onDeleted: (id: string) => void
  onMoved: () => void
}

const TYPE_LABELS: Record<NodeType, string> = {
  container: 'Container',
  agent:     'Agent',
  concept:   'Concept',
  finding:   'Finding',
  theory:    'Theory',
  synthesis: 'Synthesis',
}

const EDGE_LABELS: Record<EdgeType, string> = {
  supports:        '→ supports',
  contradicts:     '⚡ contradicts',
  prerequisite_for:'⬤ prereq for',
  extends:         '↗ extends',
  example_of:      '◎ example of',
  questions:       '? questions',
  resolves:        '✓ resolves',
  inspired:        '✦ inspired',
  connects:        '— connects',
  parent:          '↑ parent',
}

// ── Move picker overlay ────────────────────────────────────────────────────────

interface MovePickerProps {
  currentNode: NeoNode
  allNodes: GraphNode[]
  onPick: (parentId: string | null) => void
  onCancel: () => void
}

function MovePicker({ currentNode, allNodes, onPick, onCancel }: MovePickerProps) {
  const [search, setSearch] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  // Collect all descendant IDs to prevent cycles
  const descendants = new Set<string>()
  function collectDescendants(id: string) {
    for (const n of allNodes) {
      if (n.parent_id === id && !descendants.has(n.id)) {
        descendants.add(n.id)
        collectDescendants(n.id)
      }
    }
  }
  collectDescendants(currentNode.id)

  const candidates = allNodes.filter(n =>
    n.id !== currentNode.id &&
    !descendants.has(n.id) &&
    (search === '' ||
      n.title.toLowerCase().includes(search.toLowerCase()))
  ).sort((a, b) => a.title.localeCompare(b.title))

  return (
    <div className="absolute inset-0 bg-slate-900/98 z-20 flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700 flex-shrink-0">
        <span className="text-sm font-semibold text-slate-200 flex-1">Move to…</span>
        <button onClick={onCancel} className="text-slate-500 hover:text-slate-200 text-lg">✕</button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b border-slate-700/60 flex-shrink-0">
        <input
          ref={inputRef}
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search nodes…"
          className="w-full bg-slate-800 text-xs text-slate-300 placeholder-slate-600
            border border-slate-700 rounded px-2 py-1.5 outline-none focus:border-slate-500"
        />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {/* Root option */}
        <button
          className="w-full flex items-center gap-2 px-4 py-2.5 text-xs text-slate-400
            hover:bg-slate-700/50 hover:text-slate-200 transition-colors text-left border-b border-slate-800"
          onClick={() => onPick(null)}
        >
          <span className="w-2 h-2 rounded-full bg-slate-600 flex-shrink-0" />
          <span className="italic text-slate-500">No parent (root level)</span>
        </button>

        {candidates.map(n => {
          const color = nodeColor(n.node_type as NodeType)
          const isCurrent = n.id === currentNode.parent_id
          return (
            <button
              key={n.id}
              className={`w-full flex items-center gap-2 px-4 py-2 text-xs text-left
                transition-colors hover:bg-slate-700/50
                ${isCurrent ? 'text-blue-300 bg-blue-500/10' : 'text-slate-300 hover:text-slate-100'}`}
              onClick={() => onPick(n.id)}
            >
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
              <span className="truncate flex-1">{n.title}</span>
              {isCurrent && <span className="text-[10px] text-blue-500 flex-shrink-0">current</span>}
            </button>
          )
        })}

        {candidates.length === 0 && search && (
          <div className="px-4 py-6 text-xs text-slate-600 text-center">No matches</div>
        )}
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function NodePanel({ node, allNodes, onClose, onNavigate, onDeleted, onMoved }: Props) {
  const [fullNode, setFullNode] = useState<NeoNode | null>(null)
  const [edges, setEdges] = useState<NeoEdge[]>([])
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(true)

  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [showMovePicker, setShowMovePicker] = useState(false)
  const [moving, setMoving] = useState(false)

  useEffect(() => {
    setLoading(true)
    setExpanded(false)
    setConfirmDelete(false)
    setShowMovePicker(false)
    fetchNode(node.id)
      .then(({ node: n, edges: e }) => {
        setFullNode(n)
        setEdges(e)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [node.id])

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteNode(node.id)
      onDeleted(node.id)
    } catch (e) {
      setDeleteError(String(e))
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  const handleMove = async (parentId: string | null) => {
    setMoving(true)
    setShowMovePicker(false)
    try {
      await moveNode(node.id, parentId)
      onMoved()
    } catch (e) {
      console.error(e)
    } finally {
      setMoving(false)
    }
  }

  const color = nodeColor(node.node_type as NodeType)
  const confidence = Math.round((fullNode ?? node).confidence * 100)

  return (
    <div className="relative flex flex-col h-full bg-slate-900 border-l border-slate-700 overflow-hidden">

      {/* Move picker overlay */}
      {showMovePicker && (
        <MovePicker
          currentNode={node}
          allNodes={allNodes}
          onPick={handleMove}
          onCancel={() => setShowMovePicker(false)}
        />
      )}

      {/* Error banner */}
      {deleteError && (
        <div className="px-4 py-2 text-xs text-red-400 bg-red-500/10 border-b border-red-500/20 flex-shrink-0">
          ⚠ {deleteError}
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-slate-700 gap-3 flex-shrink-0">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="text-xs font-semibold px-2 py-0.5 rounded-full"
              style={{ background: color + '22', color, border: `1px solid ${color}55` }}
            >
              {TYPE_LABELS[node.node_type as NodeType] ?? node.node_type}
            </span>
          </div>
          <h2 className="text-base font-semibold text-slate-100 leading-tight">{node.title}</h2>
        </div>

        {/* Header actions */}
        <div className="flex items-center gap-1 flex-shrink-0 mt-0.5">
          {/* Move button */}
          <button
            title="Move node"
            disabled={moving}
            onClick={() => { setConfirmDelete(false); setShowMovePicker(true) }}
            className="text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-700/50 transition-colors disabled:opacity-40"
          >
            {moving
              ? <span className="text-[11px]">…</span>
              : (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M7 1v12M1 7h12M4 4l3-3 3 3M4 10l3 3 3-3" />
                </svg>
              )
            }
          </button>

          {/* Delete button */}
          {confirmDelete ? (
            <>
              <button
                disabled={deleting}
                onClick={handleDelete}
                className="text-[11px] px-2 py-0.5 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/40 transition-colors disabled:opacity-40"
              >
                {deleting ? '…' : 'Delete'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="text-[11px] px-2 py-0.5 rounded text-slate-500 hover:text-slate-300 transition-colors"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              title="Delete node"
              onClick={() => { setShowMovePicker(false); setConfirmDelete(true) }}
              className="text-slate-500 hover:text-red-400 p-1 rounded hover:bg-red-500/10 transition-colors"
            >
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                <path d="M2 3.5h9M5 3.5V2.5a.5.5 0 01.5-.5h2a.5.5 0 01.5.5v1M10.5 3.5l-.5 7a.5.5 0 01-.5.5H4a.5.5 0 01-.5-.5l-.5-7" />
                <path d="M5.5 6v3M7.5 6v3" />
              </svg>
            </button>
          )}

          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-700/50 transition-colors"
            aria-label="Close"
          >✕</button>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="px-4 pt-3 pb-2 flex-shrink-0">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-slate-500">Confidence</span>
          <span className="text-xs font-mono text-slate-300">{confidence}%</span>
        </div>
        <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all"
            style={{ width: `${confidence}%`, background: color }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4 space-y-4">
        {/* Summary */}
        {node.summary && (
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500 mb-1">Summary</p>
            <p className="text-sm text-slate-300 font-mono leading-relaxed bg-slate-800 rounded px-3 py-2">
              {node.summary}
            </p>
          </div>
        )}

        {/* Full content */}
        {loading && (
          <p className="text-sm text-slate-500 italic">Loading…</p>
        )}
        {!loading && fullNode?.content && (
          <div>
            <button
              className="text-xs uppercase tracking-wide text-slate-500 hover:text-slate-300 flex items-center gap-1"
              onClick={() => setExpanded(e => !e)}
            >
              <span>{expanded ? '▾' : '▸'}</span> Content
            </button>
            {expanded && (
              <p className="mt-2 text-sm text-slate-200 leading-relaxed whitespace-pre-wrap bg-slate-800/50 rounded px-3 py-2">
                {fullNode.content}
              </p>
            )}
          </div>
        )}

        {/* Domain / parent */}
        {(node.domain || node.parent_id) && (
          <div className="flex gap-4 text-xs text-slate-500">
            {node.domain && <span>Domain: <span className="text-slate-300">{node.domain}</span></span>}
            {node.parent_id && (
              <button
                className="hover:text-slate-200"
                onClick={() => onNavigate(node.parent_id!)}
              >
                Parent →
              </button>
            )}
          </div>
        )}

        {/* Edges */}
        {edges.length > 0 && (
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500 mb-2">Connections</p>
            <ul className="space-y-1.5">
              {edges.map(edge => {
                const isOut = edge.from_node_id === node.id
                const otherId = isOut ? edge.to_node_id : edge.from_node_id
                const edgeColor = EDGE_COLORS[edge.edge_type] ?? '#6b7280'
                const label = EDGE_LABELS[edge.edge_type as EdgeType] ?? edge.edge_type
                return (
                  <li key={edge.id} className="flex items-start gap-2">
                    <span
                      className="text-xs mt-0.5 flex-shrink-0 font-mono"
                      style={{ color: edgeColor }}
                    >
                      {isOut ? label : `← ${edge.edge_type}`}
                    </span>
                    <button
                      className="text-xs text-slate-300 hover:text-white text-left truncate flex-1"
                      title={edge.description}
                      onClick={() => onNavigate(otherId)}
                    >
                      {otherId.slice(0, 8)}…
                      {edge.description && (
                        <span className="text-slate-500 ml-1">— {edge.description.slice(0, 60)}</span>
                      )}
                    </button>
                    <span
                      className="text-xs text-slate-600 flex-shrink-0"
                      title="Edge weight"
                    >
                      {Math.round(edge.weight * 100)}%
                    </span>
                  </li>
                )
              })}
            </ul>
          </div>
        )}

        {/* Metadata */}
        <div className="text-xs text-slate-600 border-t border-slate-800 pt-3 space-y-0.5">
          <div>ID: <span className="font-mono">{node.id}</span></div>
          <div>Created: {new Date(node.created_at).toLocaleString()}</div>
          <div>Updated: {new Date(node.updated_at).toLocaleString()}</div>
        </div>
      </div>
    </div>
  )
}
