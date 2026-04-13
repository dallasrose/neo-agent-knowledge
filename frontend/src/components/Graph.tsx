import { useRef, useEffect, useCallback, useMemo, useState, forwardRef, useImperativeHandle } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import type { GraphNode, GraphLink, NeoSpark, NodeType } from '../types'
import { nodeColor, blendHex, sparkBlendFactor, SPARK_COLOR, SPARK_GOLD, SPARK_COLOR_RESOLVED, EDGE_COLORS } from '../colors'

export interface GraphHandle {
  focusNode: (id: string) => void
}

interface Props {
  nodes: GraphNode[]
  links: GraphLink[]
  sparks: NeoSpark[]
  sparkNodeCounts: Record<string, number>
  highlightIds?: Set<string>
  selectedId?: string | null
  onNodeClick: (node: GraphNode) => void
  onBackgroundClick: () => void
}

const NODE_BASE_SIZE = 5
const SPARK_SIZE = 3

// Cache geometries and materials for performance
const sphereGeo = new THREE.SphereGeometry(1, 16, 12)

function makeNodeObject(node: GraphNode, isSelected: boolean, isHighlighted: boolean, sparkCount: number, depth: number): THREE.Mesh {
  const base = nodeColor(node.node_type as NodeType)
  const color = sparkCount > 0 ? blendHex(base, SPARK_GOLD, sparkBlendFactor(sparkCount)) : base
  // Deeper in the hierarchy = smaller; roots and agent-level nodes stand out
  const depthScale = depth === 0 ? 3.2 : depth === 1 ? 2.0 : 1.0
  const sparkScale = 1 + Math.min(sparkCount, 8) * 0.08
  const size = NODE_BASE_SIZE * (0.6 + node.confidence * 0.4) * depthScale * sparkScale
  const emissiveIntensity = isSelected ? 0.7 : isHighlighted ? 0.4 : 0.15

  const mat = new THREE.MeshLambertMaterial({
    color,
    emissive: color,
    emissiveIntensity,
    transparent: !isSelected && !isHighlighted,
    opacity: isHighlighted || isSelected ? 1.0 : 0.85,
  })
  const mesh = new THREE.Mesh(sphereGeo, mat)
  mesh.scale.setScalar(size)
  return mesh
}

function endpointId(endpoint: string | GraphNode): string {
  return typeof endpoint === 'string' ? endpoint : endpoint.id
}

function makeSparkObject(spark: NeoSpark): THREE.Mesh {
  const color = spark.status === 'resolved' ? SPARK_COLOR_RESOLVED : SPARK_COLOR
  const mat = new THREE.MeshLambertMaterial({
    color,
    emissive: color,
    emissiveIntensity: spark.status === 'active' ? 0.6 : 0.1,
    transparent: true,
    opacity: spark.status === 'resolved' ? 0.35 : 0.9,
  })
  const mesh = new THREE.Mesh(sphereGeo, mat)
  mesh.scale.setScalar(SPARK_SIZE * (0.5 + spark.priority * 0.5))
  return mesh
}

const Graph = forwardRef<GraphHandle, Props>(function Graph({
  nodes,
  links,
  sparks,
  sparkNodeCounts,
  highlightIds,
  selectedId,
  onNodeClick,
  onBackgroundClick,
}, ref) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ width: 800, height: 600 })
  const controlsReady = useRef(false)

  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setDims({ width, height })
    })
    obs.observe(containerRef.current)
    const { width, height } = containerRef.current.getBoundingClientRect()
    if (width > 0 && height > 0) setDims({ width, height })
    return () => obs.disconnect()
  }, [])

  // Shared zoom-to-position logic.
  // Approaches from the camera's current direction so it feels natural regardless
  // of where the node sits in the scene.
  const zoomToXYZ = useCallback((x: number, y: number, z: number) => {
    if (!fgRef.current) return
    const ZOOM_DIST = 80
    const cam = fgRef.current.camera()
    // Vector from node → current camera
    const dx = cam.position.x - x
    const dy = cam.position.y - y
    const dz = cam.position.z - z
    const mag = Math.hypot(dx, dy, dz)
    // Place camera exactly ZOOM_DIST away along the same approach direction
    const newPos = mag > 0.01
      ? { x: x + (dx / mag) * ZOOM_DIST, y: y + (dy / mag) * ZOOM_DIST, z: z + (dz / mag) * ZOOM_DIST }
      : { x: x + ZOOM_DIST, y, z }
    fgRef.current.cameraPosition(newPos, { x, y, z }, 800)
  }, [])

  // Expose focusNode() to parent via ref
  useImperativeHandle(ref, () => ({
    focusNode: (id: string) => {
      if (!fgRef.current) return
      const found = (fgRef.current.graphData().nodes as Array<GraphNode & { x?: number; y?: number; z?: number }>)
        .find(n => n.id === id)
      if (found) zoomToXYZ(found.x ?? 0, found.y ?? 0, found.z ?? 0)
    },
  }), [zoomToXYZ])

  // Configure OrbitControls once the engine is ready:
  // - Left drag → pan  (default is rotate)
  // - Scroll zoom centered on cursor
  const configureControls = useCallback(() => {
    if (controlsReady.current) return
    const controls = fgRef.current?.controls()
    if (!controls) return
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.ROTATE,
    }
    controls.zoomToCursor = true
    controlsReady.current = true
  }, [])

  // Build combined node list: real nodes + spark pseudo-nodes
  const sparkNodes = useMemo<GraphNode[]>(() =>
    sparks.map(s => ({
      id: `spark:${s.id}`,
      node_type: 'question' as NodeType,
      title: s.description,
      summary: s.description,
      confidence: s.priority,
      domain: null,
      parent_id: null,
      source_id: null,
      spark_id: s.id,
      created_at: s.created_at,
      updated_at: s.created_at,
      consolidation_version: 0,
      _isSpark: true,
      _sparkData: s,
    } as GraphNode & { _isSpark: boolean; _sparkData: NeoSpark })
  ), [sparks])

  // Spark → target node links
  const sparkLinks = useMemo<GraphLink[]>(() =>
    sparks
      .filter(s => s.target_node_id)
      .map(s => ({
        id: `spark-link:${s.id}`,
        source: `spark:${s.id}`,
        target: s.target_node_id!,
        edge_type: 'questions' as const,
        description: s.description,
        weight: s.priority,
      }))
  , [sparks])

  const allNodes = useMemo(() => [...nodes, ...sparkNodes], [nodes, sparkNodes])
  const allLinks = useMemo(() => [...links, ...sparkLinks], [links, sparkLinks])

  // Compute depth of each node from the hierarchy (BFS from roots)
  const depthMap = useMemo<Map<string, number>>(() => {
    const map = new Map<string, number>()
    const childrenOf = new Map<string | null, string[]>()
    for (const n of nodes) {
      const pid = n.parent_id ?? null
      if (!childrenOf.has(pid)) childrenOf.set(pid, [])
      childrenOf.get(pid)!.push(n.id)
    }
    const queue: [string, number][] = (childrenOf.get(null) ?? []).map(id => [id, 0])
    while (queue.length) {
      const [id, depth] = queue.shift()!
      map.set(id, depth)
      for (const childId of childrenOf.get(id) ?? []) {
        queue.push([childId, depth + 1])
      }
    }
    return map
  }, [nodes])

  const nodeThreeObject = useCallback((node: object) => {
    const n = node as GraphNode & { _isSpark?: boolean; _sparkData?: NeoSpark }
    if (n._isSpark && n._sparkData) {
      return makeSparkObject(n._sparkData)
    }
    const isSelected = n.id === selectedId
    const isHighlighted = !highlightIds || highlightIds.size === 0 || highlightIds.has(n.id)
    const sparkCount = sparkNodeCounts[n.id] ?? 0
    const depth = depthMap.get(n.id) ?? 2
    return makeNodeObject(n, isSelected, isHighlighted, sparkCount, depth)
  }, [selectedId, highlightIds, sparkNodeCounts, depthMap])

  useEffect(() => {
    const linkForce = fgRef.current?.d3Force?.('link')
    if (!linkForce?.strength) return
    linkForce.strength((link: GraphLink) => {
      const sourceCount = sparkNodeCounts[endpointId(link.source)] ?? 0
      const targetCount = sparkNodeCounts[endpointId(link.target)] ?? 0
      const absorbed = Math.max(sourceCount, targetCount)
      if (link.id?.startsWith('spark-link:')) return 0.12
      if (link.edge_type === 'parent') return 0.45 + Math.min(absorbed, 8) * 0.04
      return 0.25 + Math.min(absorbed, 8) * 0.05
    })
    fgRef.current?.d3ReheatSimulation?.()
  }, [allLinks, sparkNodeCounts])

  const linkColor = useCallback((link: object) => {
    const l = link as GraphLink
    if (l.id?.startsWith('spark-link:')) return SPARK_COLOR + '66'
    return (EDGE_COLORS[l.edge_type] ?? '#6b7280') + '99'
  }, [])

  const linkWidth = useCallback((link: object) => {
    const l = link as GraphLink
    const sourceCount = sparkNodeCounts[endpointId(l.source)] ?? 0
    const targetCount = sparkNodeCounts[endpointId(l.target)] ?? 0
    const absorbed = Math.max(sourceCount, targetCount)
    return l.id?.startsWith('spark-link:') ? 0.3 : l.weight * (1.5 + Math.min(absorbed, 8) * 0.08)
  }, [sparkNodeCounts])

  // Zoom close to a node and open its detail panel
  const handleNodeClick = useCallback((node: object) => {
    const n = node as GraphNode & { x?: number; y?: number; z?: number }
    zoomToXYZ(n.x ?? 0, n.y ?? 0, n.z ?? 0)
    onNodeClick(n)
  }, [onNodeClick, zoomToXYZ])

  // Auto-zoom to fit on initial load
  useEffect(() => {
    const timer = setTimeout(() => {
      fgRef.current?.zoomToFit(600, 80)
    }, 800)
    return () => clearTimeout(timer)
  }, [allNodes.length])

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
      <ForceGraph3D
        ref={fgRef}
        graphData={{ nodes: allNodes, links: allLinks }}
        backgroundColor="#050a14"
        nodeThreeObject={nodeThreeObject}
        nodeThreeObjectExtend={false}
        nodeLabel={(n: object) => {
          const node = n as GraphNode
          return `<div style="background:#0f172a;color:#e2e8f0;padding:4px 8px;border-radius:4px;font-size:12px;max-width:220px;border:1px solid #334155">${node.title}</div>`
        }}
        linkColor={linkColor}
        linkWidth={linkWidth}
        linkOpacity={0.6}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkDirectionalParticles={1}
        linkDirectionalParticleWidth={0.8}
        onEngineStop={configureControls}
        onNodeClick={handleNodeClick}
        onBackgroundClick={onBackgroundClick}
        enableNodeDrag={true}
        enableNavigationControls={true}
        showNavInfo={false}
        width={dims.width}
        height={dims.height}
      />
    </div>
  )
})

export default Graph
