import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph from 'force-graph'
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react'
import { cn } from '@/lib/utils'
import { typeColor } from './ArticleList'

// Ported from web/src/Wiki.jsx. force-graph supplies wheel zoom, pan and node
// drag; on top of that this adds hover-neighbourhood spotlighting, degree-based
// node sizes, clickable type filters and explicit zoom buttons.
export function GraphView({ nodes, edges, selected, onSelect }) {
  const hostRef = useRef(null)
  const fgRef = useRef(null)
  const dataRef = useRef(null)
  const selectedRef = useRef(selected)
  const onSelectRef = useRef(onSelect)
  const [hidden, setHidden] = useState(() => new Set())
  const [showStale, setShowStale] = useState(true)

  // Held in refs so picking an article never tears down and re-simulates the
  // graph. The canvas repaints every frame, so it reads the current value.
  useEffect(() => {
    selectedRef.current = selected
    onSelectRef.current = onSelect
  })

  const types = useMemo(() => [...new Set(nodes.map((n) => n.type))].sort(), [nodes])

  useEffect(() => {
    const el = hostRef.current
    if (!el) return

    const simNodes = nodes.map((n) => ({ ...n }))
    const simLinks = edges.map((e) => ({ source: e.source, target: e.target }))
    dataRef.current = { nodes: simNodes, links: simLinks }

    const adjacency = new Map()
    const degree = new Map()
    for (const { source, target } of edges) {
      for (const [from, to] of [[source, target], [target, source]]) {
        let neighbours = adjacency.get(from)
        if (!neighbours) adjacency.set(from, (neighbours = new Set()))
        neighbours.add(to)
        degree.set(from, (degree.get(from) || 0) + 1)
      }
    }
    const radiusOf = (n) => Math.sqrt(1 + Math.min(degree.get(n.id) || 0, 14) * 0.6) * 3
    const lit = { nodes: new Set(), links: new Set() }

    const fg = ForceGraph()(el)
      .width(el.clientWidth)
      .height(el.clientHeight)
      .backgroundColor('rgba(0,0,0,0)')
      .graphData({ nodes: simNodes, links: simLinks })
      .nodeId('id')
      .nodeLabel((n) => `${n.title} — ${n.type}${n.stale ? ' · stale' : ''}`)
      .nodeRelSize(3)
      .nodeVal((n) => 1 + Math.min(degree.get(n.id) || 0, 14) * 0.6)
      .nodeColor((n) => {
        const base = typeColor(n.type)
        if (lit.nodes.size && !lit.nodes.has(n.id)) return `${base}20`
        return base + (n.stale ? '66' : 'ff')
      })
      .linkColor((l) => {
        if (!lit.links.size) return 'rgba(120,130,150,0.28)'
        return lit.links.has(l) ? 'rgba(160,185,220,0.65)' : 'rgba(120,130,150,0.06)'
      })
      .linkWidth((l) => (lit.links.has(l) ? 1.6 : 1))
      .autoPauseRedraw(false) // hover styling must repaint without sim ticks
      .nodeCanvasObjectMode(() => 'after')
      .nodeCanvasObject((n, ctx, scale) => {
        if (selectedRef.current === n.id) {
          ctx.beginPath()
          ctx.arc(n.x, n.y, radiusOf(n) + 3 / Math.sqrt(scale), 0, 2 * Math.PI)
          ctx.strokeStyle = '#6ea8fe'
          ctx.lineWidth = 1.4 / Math.sqrt(scale)
          ctx.stroke()
        }
        const spotlit = lit.nodes.has(n.id)
        if (scale < 1.25 && !spotlit) return // labels fade in with zoom, or on hover
        if (lit.nodes.size && !spotlit) return
        ctx.font = `${11 / Math.sqrt(scale)}px -apple-system, sans-serif`
        ctx.fillStyle = spotlit ? 'rgba(232,236,242,0.95)' : 'rgba(215,218,224,0.8)'
        ctx.textAlign = 'center'
        ctx.fillText(n.title, n.x, n.y + 12 / Math.sqrt(scale))
      })
      .onNodeHover((n) => {
        lit.nodes.clear()
        lit.links.clear()
        if (n) {
          lit.nodes.add(n.id)
          for (const other of adjacency.get(n.id) ?? []) lit.nodes.add(other)
          for (const l of dataRef.current.links) {
            const from = l.source.id ?? l.source
            const to = l.target.id ?? l.target
            if (from === n.id || to === n.id) lit.links.add(l)
          }
        }
        el.style.cursor = n ? 'pointer' : 'grab'
      })
      .onNodeClick((n) => onSelectRef.current?.(n.path ?? n.id))
      .cooldownTime(5000)
    fg.d3Force('charge').strength(-140)
    fg.d3Force('link').distance(65)
    const fitted = { done: false }
    fg.onEngineStop(() => {
      if (!fitted.done) { fitted.done = true; fg.zoomToFit(400, 50) }
    })
    fgRef.current = fg

    const observer = new ResizeObserver(() => fg.width(el.clientWidth).height(el.clientHeight))
    observer.observe(el)
    // Both halves matter: the observer outlives the element otherwise, and the
    // graph keeps a requestAnimationFrame loop running until _destructor runs.
    return () => {
      observer.disconnect()
      fg._destructor?.()
      fgRef.current = null
      dataRef.current = null
    }
  }, [nodes, edges])

  useEffect(() => {
    const fg = fgRef.current
    const data = dataRef.current
    if (!fg || !data) return
    // reuse the SAME node objects so positions survive filtering
    const keep = new Set(data.nodes
      .filter((n) => !hidden.has(n.type) && (showStale || !n.stale))
      .map((n) => n.id))
    fg.graphData({
      nodes: data.nodes.filter((n) => keep.has(n.id)),
      links: data.links.filter((l) =>
        keep.has(l.source.id ?? l.source) && keep.has(l.target.id ?? l.target)),
    })
  }, [hidden, showStale])

  // Programmatic fg.zoom() is overridden while the sim's warmup auto-fit is
  // active (every graphData/filter change reheats it). A synthetic wheel event
  // rides the exact code path of the user's trackpad, which works — and
  // latches auto-fit off — in every engine state.
  const wheelZoom = (direction) => {
    const canvas = hostRef.current?.querySelector('canvas')
    if (!canvas) return
    const box = canvas.getBoundingClientRect()
    canvas.dispatchEvent(new WheelEvent('wheel', {
      deltaY: direction * -46, ctrlKey: true, bubbles: true, cancelable: true,
      clientX: box.x + box.width / 2, clientY: box.y + box.height / 2,
    }))
  }

  const fitToView = () => {
    const fg = fgRef.current
    if (!fg) return
    fg.cooldownTicks(0) // silence warmup auto-fit so the fit sticks
    fg.zoomToFit(400, 50)
    setTimeout(() => fg.cooldownTicks(Infinity), 500)
  }

  const toggleType = (type) => setHidden((prev) => {
    const next = new Set(prev)
    if (next.has(type)) next.delete(type)
    else next.add(type)
    return next
  })

  const legendButton = 'flex items-center gap-1.5 rounded-full border border-border bg-card px-2 py-px text-xs hover:border-primary'

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border border-border bg-background">
      <div ref={hostRef} className="absolute inset-0 cursor-grab" />

      <div className="pointer-events-none absolute inset-x-2 bottom-2 flex flex-wrap gap-1">
        {types.map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => toggleType(type)}
            aria-pressed={!hidden.has(type)}
            className={cn(legendButton, 'pointer-events-auto', hidden.has(type) ? 'text-muted-foreground opacity-50' : 'text-foreground')}
          >
            <span className="size-2 rounded-full" style={{ background: typeColor(type) }} aria-hidden />
            {type}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setShowStale((on) => !on)}
          aria-pressed={showStale}
          className={cn(legendButton, 'pointer-events-auto', showStale ? 'text-foreground' : 'text-muted-foreground opacity-50')}
        >
          <span className="size-2 rounded-full bg-warning" aria-hidden />
          stale
        </button>
      </div>

      <div className="absolute top-2 right-2 flex flex-col gap-1">
        <button type="button" aria-label="Zoom in" onClick={() => wheelZoom(1)}
          className="flex size-6 items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:border-primary hover:text-foreground">
          <ZoomIn size={12} aria-hidden />
        </button>
        <button type="button" aria-label="Zoom out" onClick={() => wheelZoom(-1)}
          className="flex size-6 items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:border-primary hover:text-foreground">
          <ZoomOut size={12} aria-hidden />
        </button>
        <button type="button" aria-label="Fit to view" onClick={fitToView}
          className="flex size-6 items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:border-primary hover:text-foreground">
          <Maximize2 size={12} aria-hidden />
        </button>
      </div>
    </div>
  )
}
