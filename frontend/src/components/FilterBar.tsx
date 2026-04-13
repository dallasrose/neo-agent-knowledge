import { NODE_COLORS } from '../colors'
import type { NodeType } from '../types'

const NODE_TYPES: NodeType[] = ['container', 'agent', 'concept', 'finding', 'theory', 'synthesis']

interface Props {
  search: string
  onSearch: (q: string) => void
  onSearchSubmit: () => void
  activeTypes: Set<NodeType>
  onToggleType: (t: NodeType) => void
  nodeCount: number
  edgeCount: number
  sparkCount: number
  onRefresh: () => void
  loading: boolean
}

export default function FilterBar({
  search, onSearch, onSearchSubmit,
  activeTypes, onToggleType,
  nodeCount, edgeCount, sparkCount,
  onRefresh, loading,
}: Props) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 bg-slate-900/90 backdrop-blur border-b border-slate-700/60 flex-wrap">
      {/* Search */}
      <div className="flex items-center gap-1.5 bg-slate-800 rounded-lg px-3 py-1.5 flex-1 min-w-48 max-w-72">
        <svg className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
        </svg>
        <input
          className="bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none flex-1 w-full"
          placeholder="Search knowledge…"
          value={search}
          onChange={e => onSearch(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onSearchSubmit()}
        />
        {search && (
          <button className="text-slate-500 hover:text-slate-300 text-xs" onClick={() => onSearch('')}>✕</button>
        )}
      </div>

      {/* Node type filters */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {NODE_TYPES.map(type => {
          const active = activeTypes.has(type)
          const color = NODE_COLORS[type]
          return (
            <button
              key={type}
              onClick={() => onToggleType(type)}
              className="text-xs px-2.5 py-1 rounded-full transition-all font-medium"
              style={{
                background: active ? color + '22' : 'transparent',
                color: active ? color : '#64748b',
                border: `1px solid ${active ? color + '55' : '#334155'}`,
              }}
            >
              {type}
            </button>
          )
        })}
      </div>

      {/* Stats */}
      <div className="text-xs text-slate-600 flex items-center gap-3 ml-auto flex-shrink-0">
        <span>{nodeCount} nodes</span>
        <span>{edgeCount} edges</span>
        <span className="text-amber-600/80">{sparkCount} sparks</span>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="text-slate-500 hover:text-slate-200 disabled:opacity-40 transition-colors"
          title="Refresh"
        >
          <svg className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>
    </div>
  )
}
