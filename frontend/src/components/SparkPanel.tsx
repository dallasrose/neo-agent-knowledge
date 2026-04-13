import type { NeoSpark, GraphNode } from '../types'

interface Props {
  spark: NeoSpark
  nodeById: Map<string, GraphNode>
  onClose: () => void
  onNavigate: (id: string) => void
}

const STATUS_COLORS: Record<string, string> = {
  active:    'text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
  resolved:  'text-green-400 bg-green-400/10 border-green-400/30',
  abandoned: 'text-slate-400 bg-slate-400/10 border-slate-400/30',
}

const TYPE_LABELS: Record<string, string> = {
  contradiction: 'Contradiction',
  gap:           'Gap',
  question:      'Question',
  inconsistency: 'Inconsistency',
  opportunity:   'Opportunity',
}

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function SparkPanel({ spark, nodeById, onClose, onNavigate }: Props) {
  const targetNode = spark.target_node_id ? nodeById.get(spark.target_node_id) : null
  const statusCls = STATUS_COLORS[spark.status] ?? STATUS_COLORS.abandoned

  return (
    <div className="flex flex-col h-full bg-slate-900 text-slate-200 text-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-slate-700 gap-3 flex-shrink-0">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-yellow-400 font-bold text-xs tracking-widest uppercase">✦ Spark</span>
            <span className={`text-xs px-2 py-0.5 rounded-full border ${statusCls}`}>
              {spark.status}
            </span>
          </div>
          <span className="text-xs text-slate-400">
            {TYPE_LABELS[spark.spark_type] ?? spark.spark_type}
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-slate-500 hover:text-slate-300 flex-shrink-0 text-lg leading-none"
        >×</button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Description */}
        <div>
          <p className="text-slate-100 leading-relaxed">{spark.description}</p>
        </div>

        {/* Priority bar */}
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>Priority</span>
            <span>{Math.round(spark.priority * 100)}%</span>
          </div>
          <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full bg-yellow-400"
              style={{ width: `${spark.priority * 100}%` }}
            />
          </div>
        </div>

        {/* Target node */}
        {spark.target_node_id && (
          <div>
            <div className="text-xs text-slate-500 mb-1">Target node</div>
            {targetNode ? (
              <button
                onClick={() => onNavigate(spark.target_node_id!)}
                className="text-left w-full px-3 py-2 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs transition-colors"
              >
                {targetNode.title}
              </button>
            ) : (
              <span className="text-xs text-slate-500 italic">Node not in current view</span>
            )}
          </div>
        )}

        {!spark.target_node_id && (
          <div className="text-xs text-slate-500 italic">No target node (orphaned)</div>
        )}

        {/* Dates */}
        <div className="text-xs text-slate-500 space-y-1 pt-2 border-t border-slate-800">
          <div>Created {fmt(spark.created_at)}</div>
          {spark.resolved_at && <div>Resolved {fmt(spark.resolved_at)}</div>}
        </div>
      </div>
    </div>
  )
}
