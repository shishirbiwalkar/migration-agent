'use client'

import { useState, useEffect, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import {
  FlaskConical, ArrowLeft, Loader2, AlertTriangle,
  CheckCircle2, XCircle, Send, Bot, RefreshCw,
  ChevronRight,
} from 'lucide-react'

const API = 'http://localhost:8001'

interface Well {
  staging_id:   number
  well_position: string
  signal:        number | null
  risk_level:    'auto' | 'review'
  status:        'pending' | 'excluded' | 'approved' | 'auto_approved'
}

interface ScientistContext {
  status:         string
  scientist_name: string
  total_wells:    number
  pending_wells:  number
  excluded_wells: number
  approved_wells: number
  wells:          Well[]
}

interface Action {
  action:         'approved' | 'excluded' | 'excluded_all'
  staging_id?:    number
  well_position?: string
  excluded_count?: number
}

interface AgentResult {
  trace_id:       string
  scientist_name: string
  result:         string
  actions_taken:  Action[]
  turns_used:     number
  abase_deleted:  number
}

// ── Well status badge ─────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: Well['status'] }) {
  const styles: Record<string, string> = {
    pending:      'bg-amber-50 text-amber-700 ring-amber-200',
    excluded:     'bg-red-50 text-red-600 ring-red-200',
    approved:     'bg-emerald-50 text-emerald-700 ring-emerald-200',
    auto_approved:'bg-blue-50 text-blue-600 ring-blue-200',
  }
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ring-1 ${styles[status] ?? ''}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

// ── Well list ─────────────────────────────────────────────────────────────────

function WellList({ ctx, loading }: { ctx: ScientistContext | null; loading: boolean }) {
  if (loading) return (
    <div className="flex items-center justify-center h-40">
      <Loader2 size={20} className="animate-spin text-blue-500" />
    </div>
  )

  if (!ctx) return null

  const pending  = ctx.wells.filter(w => w.status === 'pending')
  const resolved = ctx.wells.filter(w => w.status !== 'pending')

  return (
    <div className="space-y-2">
      {pending.length > 0 && (
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-amber-600 mb-1.5">
            Pending ({pending.length})
          </p>
          <div className="space-y-1">
            {pending.map(w => (
              <div key={w.staging_id}
                className="flex items-center gap-3 bg-white border border-amber-200 rounded-lg px-3 py-2">
                <span className="font-mono text-sm font-bold text-gray-800 w-10">{w.well_position}</span>
                <span className={`font-mono text-sm flex-1 font-semibold ${
                  w.signal != null && (w.signal < 1.0 || w.signal > 20.0)
                    ? 'text-red-600' : 'text-blue-700'
                }`}>
                  {w.signal != null ? w.signal.toFixed(3) : '—'}
                  {w.signal != null && (w.signal < 1.0 || w.signal > 20.0) && (
                    <span className="ml-1 text-[10px] text-red-400 font-normal">⚠ anomalous</span>
                  )}
                </span>
                <StatusBadge status={w.status} />
              </div>
            ))}
          </div>
        </div>
      )}

      {resolved.length > 0 && (
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-1.5 mt-3">
            Resolved ({resolved.length})
          </p>
          <div className="space-y-1">
            {resolved.map(w => (
              <div key={w.staging_id}
                className={`flex items-center gap-3 border rounded-lg px-3 py-2 opacity-70 ${
                  w.status === 'excluded' ? 'bg-red-50/50 border-red-200' : 'bg-emerald-50/50 border-emerald-200'
                }`}>
                <span className="font-mono text-sm font-bold text-gray-500 w-10 line-through">{w.well_position}</span>
                <span className="font-mono text-sm text-gray-400 flex-1">
                  {w.signal != null ? w.signal.toFixed(3) : '—'}
                </span>
                <StatusBadge status={w.status} />
              </div>
            ))}
          </div>
        </div>
      )}

      {ctx.wells.length === 0 && (
        <div className="text-center py-8 text-gray-400 text-sm">No wells found.</div>
      )}
    </div>
  )
}

// ── Action summary ────────────────────────────────────────────────────────────

function ActionSummary({ actions }: { actions: Action[] }) {
  if (!actions.length) return null
  return (
    <div className="mt-3 space-y-1">
      {actions.map((a, i) => (
        <div key={i} className={`flex items-center gap-2 text-xs px-2 py-1 rounded-lg ${
          a.action === 'approved' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'
        }`}>
          {a.action === 'approved'
            ? <CheckCircle2 size={12} className="shrink-0" />
            : <XCircle size={12} className="shrink-0" />}
          {a.action === 'excluded_all'
            ? `Excluded all wells (${a.excluded_count ?? 0} rows)`
            : a.action === 'approved'
              ? `Approved well ${a.well_position ?? `#${a.staging_id}`}`
              : `Excluded well #${a.staging_id}`}
        </div>
      ))}
    </div>
  )
}

// ── Main advisor view ─────────────────────────────────────────────────────────

function AdvisorView() {
  const searchParams   = useSearchParams()
  const router         = useRouter()
  const traceId        = searchParams.get('trace_id') ?? ''
  const scientistName  = searchParams.get('scientist') ?? ''

  const [ctx,       setCtx]       = useState<ScientistContext | null>(null)
  const [ctxLoading,setCtxLoading] = useState(false)
  const [ctxError,  setCtxError]  = useState<string | null>(null)

  const [message,   setMessage]   = useState('')
  const [running,   setRunning]   = useState(false)
  const [agentResult, setAgentResult] = useState<AgentResult | null>(null)
  const [runError,  setRunError]  = useState<string | null>(null)

  const fetchContext = async () => {
    if (!traceId || !scientistName) return
    setCtxLoading(true); setCtxError(null)
    try {
      const res = await fetch(
        `${API}/api/reviewer/${traceId}/${encodeURIComponent(scientistName)}`)
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail ?? `HTTP ${res.status}`)
      }
      setCtx(await res.json())
    } catch (e: unknown) {
      setCtxError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setCtxLoading(false)
    }
  }

  useEffect(() => { fetchContext() }, [traceId, scientistName])

  const handleRun = async () => {
    if (!message.trim() || running) return
    setRunning(true); setRunError(null); setAgentResult(null)
    try {
      const res = await fetch(
        `${API}/api/reviewer/${traceId}/${encodeURIComponent(scientistName)}/run`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: message.trim() }),
        },
      )
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail ?? `HTTP ${res.status}`)
      }
      const data: AgentResult = await res.json()
      setAgentResult(data)
      setMessage('')
      await fetchContext()
    } catch (e: unknown) {
      setRunError(e instanceof Error ? e.message : 'Agent failed')
    } finally {
      setRunning(false)
    }
  }

  const allResolved = ctx?.pending_wells === 0

  if (!traceId || !scientistName) return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <AlertTriangle size={32} className="text-amber-400 mx-auto mb-3" />
        <p className="text-gray-600 font-medium">Missing trace_id or scientist in URL.</p>
        <p className="text-xs text-gray-400 mt-1">Expected: /reviewer?trace_id=...&scientist=...</p>
        <button onClick={() => router.push('/')}
          className="mt-4 text-sm text-blue-600 underline">← Back</button>
      </div>
    </div>
  )

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden"
      style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>

      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="px-4 py-5 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 bg-blue-700 rounded-lg flex items-center justify-center">
              <FlaskConical size={14} className="text-white" strokeWidth={2.5} />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900 leading-none">Migration Agent</p>
              <p className="text-[10px] text-gray-400 mt-0.5">Reviewer</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          <button onClick={() => router.push(`/review?trace_id=${traceId}`)}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-gray-500 hover:bg-gray-100 transition-colors">
            <ArrowLeft size={14} /> Back to Review
          </button>
          <div className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm font-medium bg-blue-50 text-blue-700">
            <Bot size={15} className="text-blue-600" /> Advisor
          </div>
        </nav>

        <div className="px-4 py-4 border-t border-gray-100 space-y-3">
          <div>
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Scientist</p>
            <p className="text-sm font-semibold text-gray-800">{scientistName}</p>
          </div>
          {ctx && (
            <div className="space-y-1.5">
              {[
                { label: 'Pending',  value: ctx.pending_wells,  color: 'text-amber-600' },
                { label: 'Approved', value: ctx.approved_wells, color: 'text-emerald-600' },
                { label: 'Excluded', value: ctx.excluded_wells, color: 'text-red-500' },
              ].map(({ label, value, color }) => (
                <div key={label} className="flex justify-between text-xs">
                  <span className="text-gray-500">{label}</span>
                  <span className={`font-bold ${color}`}>{value}</span>
                </div>
              ))}
            </div>
          )}
          <div className="bg-gray-50 border border-gray-200 rounded-lg px-2 py-1.5">
            <p className="text-[10px] font-mono text-gray-400 break-all">{traceId.slice(0,20)}…</p>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <div className="px-8 py-6 max-w-5xl mx-auto">

            {/* Header */}
            <div className="flex items-start justify-between mb-6">
              <div>
                <h1 className="text-xl font-bold text-gray-900">
                  Reviewer
                  <span className="ml-2 text-gray-400 font-normal">·</span>
                  <span className="ml-2 text-gray-600 font-semibold">{scientistName}</span>
                </h1>
                <p className="text-xs text-gray-400 mt-1">
                  Describe what the scientist communicated. The agent will resolve their pending wells.
                </p>
              </div>
              <button onClick={fetchContext} disabled={ctxLoading}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50">
                <RefreshCw size={11} className={ctxLoading ? 'animate-spin' : ''} />
                Refresh
              </button>
            </div>

            <div className="grid grid-cols-2 gap-6">

              {/* Left: Well list */}
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                    Staging Wells
                  </h2>
                  {allResolved && (
                    <span className="flex items-center gap-1 text-[10px] font-bold text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                      <CheckCircle2 size={10} /> All resolved
                    </span>
                  )}
                </div>

                {ctxError ? (
                  <div className="bg-red-50 border border-red-200 rounded-xl p-4">
                    <p className="text-sm text-red-700 font-medium">Error loading wells</p>
                    <p className="text-xs text-red-500 mt-1">{ctxError}</p>
                  </div>
                ) : (
                  <WellList ctx={ctx} loading={ctxLoading} />
                )}
              </div>

              {/* Right: Chat interface */}
              <div className="flex flex-col gap-4">
                <h2 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                  Scientist&rsquo;s Message
                </h2>

                {/* Context hint */}
                {ctx && ctx.pending_wells > 0 && (
                  <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
                    <p className="text-xs text-blue-700">
                      <strong>{ctx.pending_wells} pending wells</strong> need resolution.
                      Describe what {scientistName} communicated — the agent will approve or exclude wells accordingly.
                    </p>
                    <div className="mt-2 space-y-1">
                      {[
                        '"B04 had a calibration error, rest of the data is fine"',
                        '"She confirmed all readings are valid"',
                        '"Equipment failure — do not migrate any of my data"',
                      ].map((ex, i) => (
                        <button key={i} onClick={() => setMessage(ex.slice(1, -1))}
                          className="flex items-center gap-1.5 text-[11px] text-blue-600 hover:text-blue-800 hover:underline transition-colors text-left">
                          <ChevronRight size={10} />
                          {ex}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Message input */}
                <div className="space-y-2">
                  <textarea
                    value={message}
                    onChange={e => setMessage(e.target.value)}
                    placeholder={`What did ${scientistName} say?`}
                    disabled={running || allResolved}
                    rows={5}
                    className="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm text-gray-800 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition disabled:opacity-40 disabled:bg-gray-50"
                  />
                  <button
                    onClick={handleRun}
                    disabled={!message.trim() || running || allResolved}
                    className="w-full flex items-center justify-center gap-2 bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {running
                      ? <><Loader2 size={15} className="animate-spin" /> Agent reasoning…</>
                      : <><Send size={14} /> Run Review Agent</>}
                  </button>
                  {running && (
                    <p className="text-xs text-center text-gray-400">
                      Gemini is reasoning about the scientist&rsquo;s wells…
                    </p>
                  )}
                  {allResolved && !running && (
                    <p className="text-xs text-center text-gray-400">
                      All wells resolved — no pending items remain.
                    </p>
                  )}
                </div>

                {/* Run error */}
                {runError && (
                  <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 flex items-start gap-2">
                    <AlertTriangle size={14} className="text-red-500 shrink-0 mt-0.5" />
                    <p className="text-sm text-red-700">{runError}</p>
                  </div>
                )}

                {/* Agent response */}
                {agentResult && (
                  <div className="bg-gray-900 rounded-xl p-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-full bg-blue-600 flex items-center justify-center">
                        <Bot size={12} className="text-white" />
                      </div>
                      <span className="text-xs font-bold text-gray-300 uppercase tracking-wider">
                        Agent Response
                      </span>
                      <span className="text-[10px] text-gray-500 ml-auto">
                        {agentResult.turns_used} turns
                      </span>
                    </div>

                    <p className="text-sm text-gray-100 leading-relaxed whitespace-pre-wrap">
                      {agentResult.result || '(Agent completed with no text response)'}
                    </p>

                    <ActionSummary actions={agentResult.actions_taken} />

                    {agentResult.abase_deleted > 0 && (
                      <div className="flex items-center gap-2 text-xs text-emerald-400 bg-emerald-900/30 rounded-lg px-3 py-1.5">
                        <CheckCircle2 size={12} />
                        Scientist deleted from ABASE (fully migrated)
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <Loader2 size={24} className="animate-spin text-blue-500" />
      </div>
    }>
      <AdvisorView />
    </Suspense>
  )
}
