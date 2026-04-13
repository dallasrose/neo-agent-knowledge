import type { NodeType } from './types'

// Colors per spec
export const NODE_COLORS: Record<NodeType, string> = {
  container: '#475569', // slate          — structural scaffolding, deliberately muted
  agent:     '#d946ef', // neon purple    — entity, prominent
  concept:   '#93c5fd', // light blue      — named knowledge
  finding:   '#2563eb', // solid blue     — observed fact
  theory:    '#ea580c', // orange         — explanatory claim
  synthesis: '#00e5cc', // neon teal      — consolidated conclusion
}

export const SPARK_COLOR = '#fbbf24'           // amber-yellow glow (orbs)
export const SPARK_GOLD  = '#c8960c'           // deep gold — blend target for resolved sparks
export const SPARK_COLOR_RESOLVED = '#78716c'  // muted brown/gray

export const EDGE_COLORS: Record<string, string> = {
  supports:       '#22c55e',
  contradicts:    '#ef4444',
  prerequisite_for: '#f97316',
  extends:        '#3b82f6',
  example_of:     '#a855f7',
  questions:      '#eab308',
  resolves:       '#14b8a6',
  inspired:       '#ec4899',
  connects:       '#6b7280',
}

export function nodeColor(type: NodeType): string {
  return NODE_COLORS[type] ?? '#6b7280'
}

/**
 * How much spark-yellow to blend in given N resolved sparks.
 * Each spark contributes 30% of the remaining distance to yellow (diminishing returns).
 * 1 spark → 30%, 2 → 51%, 3 → 66%, 5 → 83%, 10 → 97%
 */
export function sparkBlendFactor(count: number): number {
  return 1 - Math.pow(0.70, count)
}

/** Blend two hex colors. t=0 → all a, t=1 → all b */
export function blendHex(a: string, b: string, t = 0.45): string {
  const parse = (h: string) => {
    const n = parseInt(h.replace('#', ''), 16)
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]
  }
  const [ar, ag, ab] = parse(a)
  const [br, bg, bb] = parse(b)
  const r = Math.round(ar + (br - ar) * t)
  const g = Math.round(ag + (bg - ag) * t)
  const bl = Math.round(ab + (bb - ab) * t)
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${bl.toString(16).padStart(2, '0')}`
}
