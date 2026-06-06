'use client'

import { useState, useEffect, useMemo, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import {
  ClipboardCheck, CheckCircle2, XCircle, Loader2,
  AlertTriangle, X, ArrowLeft, Users, FlaskConical,
  ChevronDown, ChevronRight, Trash2, RotateCcw, Bot, Send,
} from 'lucide-react'

const API          = 'http://localhost:8001'
const APPROVED_BY  = 'HITL Reviewer'

interface StagingRow {
  staging_id:               number
  scientist_name:           string
  scientist_role:           string
  compound_id:              string | null
  ec50_um:                  number | null
  hill_slope:               number | null
  r_squared:                number | null
  curve_quality:            string | null
  signal:                   number | null  // Emax (top of fitted curve)
  num_concentration_points: number | null
  assay_type:               string | null
  status:                   'pending' | 'excluded' | 'approved' | 'rejected' | 'auto_approved'
  risk_level:               'auto' | 'review'
}

interface StagingData {
  trace_id:     string
  row_count:    number
  auto_approved: number
  needs_review:  number
  users:        { name: string; role: string; row_count: number; avg_signal: number }[]
  rows:         StagingRow[]
}

interface Toast { type: 'success' | 'error'; message: string }
type ActionState = 'idle' | 'approving' | 'rejecting'

function ToastAlert({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => { const t = setTimeout(onDismiss, 5000); return () => clearTimeout(t) }, [onDismiss])
  return (
    <div className={`fixed top-5 right-5 z-50 flex items-center gap-3 px-4 py-3.5 rounded-xl shadow-xl border text-sm font-medium ${
      toast.type === 'success' ? 'bg-white border-green-200 text-green-800' : 'bg-white border-red-200 text-red-800'
    }`}>
      {toast.type === 'success'
        ? <CheckCircle2 size={16} className="text-green-500 shrink-0" />
        : <AlertTriangle size={16} className="text-red-500 shrink-0" />}
      {toast.message}
      <button onClick={onDismiss}><X size={13} className="text-gray-400 ml-1" /></button>
    </div>
  )
}

// ── User card with expandable row controls ─────────────────────────────────────

function UserCard({ name, role, rows, traceId, onRefresh }: {
  name: string; role: string; rows: StagingRow[]
  traceId: string; onRefresh: () => void
}) {
  const [expanded,  setExpanded]  = useState(true)
  const [removing,  setRemoving]  = useState<string | null>(null)

  const pending  = rows.filter(r => r.status === 'pending')
  const excluded = rows.filter(r => r.status === 'excluded')
  const isFullyExcluded = rows.every(r => r.status !== 'pending')

  const excludeUser = async () => {
    setRemoving('user')
    await fetch(`${API}/api/migrate/staging/${traceId}/exclude-user`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scientist_name: name }),
    })
    setRemoving(null)
    onRefresh()
  }

  const restoreUser = async () => {
    setRemoving('restore')
    await fetch(`${API}/api/migrate/staging/${traceId}/restore-user`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scientist_name: name }),
    })
    setRemoving(null)
    onRefresh()
  }

  const excludeRow = async (stagingId: number) => {
    setRemoving(`row-${stagingId}`)
    await fetch(`${API}/api/migrate/staging/row/${stagingId}`, { method: 'DELETE' })
    setRemoving(null)
    onRefresh()
  }

  const restoreRow = async (stagingId: number) => {
    setRemoving(`restore-${stagingId}`)
    await fetch(`${API}/api/migrate/staging/row/${stagingId}/restore`, { method: 'POST' })
    setRemoving(null)
    onRefresh()
  }

  return (
    <div className={`bg-white border rounded-xl overflow-hidden transition-all ${
      isFullyExcluded ? 'border-red-200 opacity-60' : 'border-gray-200'
    }`}>
      {/* User header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-gray-50 border-b border-gray-100">
        <button onClick={() => setExpanded(e => !e)} className="text-gray-400 hover:text-gray-600">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-xs">
          {name.split('_').map(p => p[0]).join('').slice(0,2)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className={`text-sm font-semibold ${isFullyExcluded ? 'line-through text-gray-400' : 'text-gray-900'}`}>
              {name}
            </p>
            {/* Anomaly flag — shown when any pending curve has poor R² */}
            {pending.some(r => r.r_squared !== null && r.r_squared !== undefined && r.r_squared < 0.90) && !isFullyExcluded && (
              <span className="flex items-center gap-1 text-[10px] font-bold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                ⚠ Poor curve fit
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500">
            {role} · {rows.length} compound(s) ·{' '}
            {pending.length > 0 && pending[0].r_squared !== null && pending[0].r_squared !== undefined ? (
              <>R² <span className={`font-semibold ${pending[0].r_squared < 0.90 ? 'text-red-600' : 'text-green-700'}`}>
                {pending[0].r_squared.toFixed(3)}
              </span></>
            ) : '—'}
          </p>
        </div>

        {/* Status chips */}
        <div className="flex items-center gap-2">
          {pending.length > 0 && (
            <span className="text-xs bg-amber-50 text-amber-700 ring-1 ring-amber-200 px-2 py-0.5 rounded-full font-semibold">
              {pending.length} pending
            </span>
          )}
          {excluded.length > 0 && (
            <span className="text-xs bg-red-50 text-red-600 ring-1 ring-red-200 px-2 py-0.5 rounded-full font-semibold">
              {excluded.length} excluded
            </span>
          )}
        </div>

        {/* Remove / restore user button */}
        {isFullyExcluded ? (
          <button onClick={restoreUser} disabled={removing === 'restore'}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-blue-600 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50">
            {removing === 'restore' ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
            Restore User
          </button>
        ) : (
          <button onClick={excludeUser} disabled={removing === 'user'}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors disabled:opacity-50">
            {removing === 'user' ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
            Remove User
          </button>
        )}
      </div>

      {/* Experiment rows */}
      {expanded && (
        <div className="divide-y divide-gray-50">
          {rows.map(row => (
            <div key={row.staging_id} className={`flex items-center gap-3 px-4 py-2.5 transition-colors ${
              row.status === 'excluded' ? 'bg-red-50/50 opacity-60' : 'hover:bg-gray-50'
            }`}>
              <div className="w-6" /> {/* indent */}
              {/* Compound ID */}
              <span className={`font-mono text-xs font-semibold w-24 truncate ${row.status === 'excluded' ? 'line-through text-gray-400' : 'text-gray-700'}`}>
                {row.compound_id ?? '—'}
              </span>
              {/* EC50 */}
              <span className={`font-mono text-sm font-semibold w-24 ${row.status === 'excluded' ? 'text-gray-400' : 'text-blue-700'}`}>
                {row.ec50_um != null ? `${row.ec50_um.toFixed(3)} µM` : '—'}
              </span>
              {/* R² with quality badge */}
              <span className={`text-sm font-semibold w-16 ${
                row.status === 'excluded' ? 'text-gray-400' :
                row.r_squared != null && row.r_squared < 0.90 ? 'text-red-600' : 'text-green-700'
              }`}>
                {row.r_squared != null ? row.r_squared.toFixed(3) : '—'}
              </span>
              {/* Curve quality chip */}
              {row.curve_quality && row.status !== 'excluded' && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${
                  row.curve_quality === 'excellent' ? 'bg-green-50 text-green-700' :
                  row.curve_quality === 'good'      ? 'bg-blue-50 text-blue-700' :
                  row.curve_quality === 'fair'      ? 'bg-amber-50 text-amber-700' :
                  'bg-red-50 text-red-600'
                }`}>
                  {row.curve_quality}
                </span>
              )}
              <span className={`ml-auto text-xs px-2 py-0.5 rounded-full font-semibold ${
                row.status === 'pending'  ? 'bg-amber-50 text-amber-700 ring-1 ring-amber-200' :
                row.status === 'excluded' ? 'bg-red-50 text-red-500 ring-1 ring-red-200' :
                'bg-green-50 text-green-700 ring-1 ring-green-200'
              }`}>
                {row.status}
              </span>

              {/* Per-row remove/restore */}
              {row.status === 'excluded' ? (
                <button onClick={() => restoreRow(row.staging_id)}
                  disabled={removing === `restore-${row.staging_id}`}
                  className="p-1.5 rounded-lg text-blue-500 hover:bg-blue-50 transition-colors disabled:opacity-50"
                  title="Restore this row">
                  {removing === `restore-${row.staging_id}`
                    ? <Loader2 size={12} className="animate-spin" />
                    : <RotateCcw size={12} />}
                </button>
              ) : row.status === 'pending' ? (
                <button onClick={() => excludeRow(row.staging_id)}
                  disabled={!!removing}
                  className="p-1.5 rounded-lg text-red-400 hover:bg-red-50 hover:text-red-600 transition-colors disabled:opacity-50"
                  title="Remove this experiment row">
                  {removing === `row-${row.staging_id}`
                    ? <Loader2 size={12} className="animate-spin" />
                    : <X size={12} />}
                </button>
              ) : <div className="w-7" />}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main HITL View ─────────────────────────────────────────────────────────────

function HITLView() {
  const searchParams = useSearchParams()
  const router       = useRouter()
  const traceId      = searchParams.get('trace_id') ?? ''

  const [data,        setData]        = useState<StagingData | null>(null)
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [action,      setAction]      = useState<ActionState>('idle')
  const [toast,       setToast]       = useState<Toast | null>(null)
  const [aiMessage,   setAiMessage]   = useState('')
  const [aiRunning,   setAiRunning]   = useState(false)
  const [aiResult,    setAiResult]    = useState<{ result: string; actions_taken: any[] } | null>(null)

  const fetchStaging = async (id: string) => {
    setLoading(true); setError(null)
    try {
      const res = await fetch(`${API}/api/migrate/staging/${id}`)
      if (!res.ok) { const b = await res.json().catch(()=>({})); throw new Error(b.detail ?? `HTTP ${res.status}`) }
      setData(await res.json())
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Failed to load') }
    finally { setLoading(false) }
  }

  useEffect(() => { if (traceId) fetchStaging(traceId) }, [traceId])

  // Group only 'review' rows by scientist — auto rows are already in GDS
  const grouped = useMemo(() => {
    if (!data) return {}
    return data.rows
      .filter(r => r.risk_level === 'review')
      .reduce<Record<string, StagingRow[]>>((acc, row) => {
        (acc[row.scientist_name] ??= []).push(row)
        return acc
      }, {})
  }, [data])

  const pendingCount  = data?.rows.filter(r => r.status === 'pending' && r.risk_level === 'review').length ?? 0
  const excludedCount = data?.rows.filter(r => r.status === 'excluded').length ?? 0
  const pendingUsers  = Object.entries(grouped).filter(([,rows]) => rows.some(r => r.status === 'pending' && r.risk_level === 'review')).length
  const batchDone     = data?.rows.filter(r => r.risk_level === 'review').every(r => r.status !== 'pending') ?? false
  const autoApproved  = data?.auto_approved ?? 0
  const needsReview   = data?.needs_review  ?? 0

  const handleApprove = async () => {
    setAction('approving')
    try {
      const res = await fetch(`${API}/api/migrate/approve/${traceId}`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ approved_by: APPROVED_BY }),
      })
      if (!res.ok) { const e = await res.json().catch(()=>({})); throw new Error(e.detail) }
      const r = await res.json()
      setToast({ type:'success', message:`✓ ${r.users_promoted} users + ${r.experiments_promoted} experiments promoted to GDS.` })
      fetchStaging(traceId)
    } catch (e: unknown) { setToast({ type:'error', message: e instanceof Error ? e.message : 'Failed' }) }
    finally { setAction('idle') }
  }

  const handleReject = async () => {
    setAction('rejecting')
    try {
      const res = await fetch(`${API}/api/migrate/reject/${traceId}`, { method:'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const r = await res.json()
      setToast({ type:'error', message:`${r.rejected_rows} rows rejected. Nothing reached GDS.` })
      fetchStaging(traceId)
    } catch (e: unknown) { setToast({ type:'error', message: e instanceof Error ? e.message : 'Failed' }) }
    finally { setAction('idle') }
  }

  const runBatchAgent = async () => {
    if (!aiMessage.trim() || aiRunning) return
    setAiRunning(true)
    setAiResult(null)
    try {
      const res = await fetch(`${API}/api/reviewer/${traceId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: aiMessage.trim(), approved_by: 'Review Agent' }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setAiResult(data)
      setAiMessage('')
      fetchStaging(traceId)
    } catch (e) {
      setToast({ type: 'error', message: e instanceof Error ? e.message : 'Agent failed' })
    } finally {
      setAiRunning(false)
    }
  }

  if (!traceId) return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <AlertTriangle size={32} className="text-amber-400 mx-auto mb-3" />
        <p className="text-gray-600 font-medium">No trace ID in URL.</p>
        <button onClick={() => router.push('/')} className="mt-4 text-sm text-blue-600 underline">← Back to upload</button>
      </div>
    </div>
  )

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden" style={{ fontFamily:'Inter, system-ui, sans-serif' }}>
      {toast && <ToastAlert toast={toast} onDismiss={() => setToast(null)} />}

      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="px-4 py-5 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 bg-blue-700 rounded-lg flex items-center justify-center">
              <FlaskConical size={14} className="text-white" strokeWidth={2.5} />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900 leading-none">Migration Agent</p>
              <p className="text-[10px] text-gray-400 mt-0.5">HITL Review</p>
            </div>
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          <button onClick={() => router.push('/')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-gray-500 hover:bg-gray-100 transition-colors">
            <ArrowLeft size={14} /> Back to Upload
          </button>
          <div className="flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm font-medium bg-blue-50 text-blue-700">
            <ClipboardCheck size={15} className="text-blue-600" /> HITL Review
          </div>
        </nav>
        <div className="px-4 py-4 border-t border-gray-100">
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            <p className="text-[10px] font-bold text-amber-700 uppercase tracking-wider">Batch</p>
            <p className="text-[10px] text-amber-600 font-mono break-all mt-0.5">{traceId.slice(0,20)}…</p>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <div className="px-8 py-6 max-w-4xl mx-auto">

            <div className="mb-6">
              <h1 className="text-xl font-bold text-gray-900">Human-in-the-Loop Review</h1>
              <p className="text-xs text-gray-500 font-mono mt-1">trace_id: {traceId}</p>
            </div>

            {loading && (
              <div className="flex flex-col items-center py-20">
                <Loader2 size={24} className="animate-spin text-blue-500 mb-3" />
                <p className="text-sm text-gray-400">Loading staged batch...</p>
              </div>
            )}

            {error && (
              <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3">
                <AlertTriangle size={16} className="text-red-500 shrink-0 mt-0.5" />
                <div><p className="text-sm font-semibold text-red-700">Error</p><p className="text-xs text-red-500">{error}</p></div>
              </div>
            )}

            {data && (
              <>
                {/* Partial HITL banner */}
                {(autoApproved > 0 || needsReview > 0) && (
                  <div className="mb-5 rounded-xl overflow-hidden border border-gray-200">
                    <div className="bg-gray-800 px-5 py-2.5 flex items-center gap-2">
                      <span className="text-xs font-bold text-white uppercase tracking-wider">Partial HITL — Agent Classification</span>
                    </div>
                    <div className="grid grid-cols-2 divide-x divide-gray-100">
                      <div className="px-5 py-4 flex items-center gap-3 bg-emerald-50">
                        <CheckCircle2 size={20} className="text-emerald-500 shrink-0" />
                        <div>
                          <p className="text-lg font-bold text-emerald-700">{autoApproved} rows</p>
                          <p className="text-xs text-emerald-600">Auto-approved by AI Agent — clean signals, already in GDS production</p>
                        </div>
                      </div>
                      <div className="px-5 py-4 flex items-center gap-3 bg-amber-50">
                        <AlertTriangle size={20} className="text-amber-500 shrink-0" />
                        <div>
                          <p className="text-lg font-bold text-amber-700">{needsReview} rows</p>
                          <p className="text-xs text-amber-600">Flagged by AI Agent — anomalous signals, need your review below</p>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Summary KPIs */}
                <div className="grid grid-cols-4 gap-3 mb-6">
                  {[
                    { label:'Total Rows',    value: data.row_count },
                    { label:'Will Migrate',  value: pendingCount,  color:'text-blue-700' },
                    { label:'Excluded',      value: excludedCount, color:'text-red-600'  },
                    { label:'Users',         value: pendingUsers,  color:'text-blue-700' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-white border border-gray-200 rounded-xl p-4 text-center shadow-sm">
                      <p className="text-[11px] uppercase tracking-widest text-gray-400 font-semibold mb-1">{label}</p>
                      <p className={`text-2xl font-bold ${color ?? 'text-gray-900'}`}>{value}</p>
                    </div>
                  ))}
                </div>

                {/* Instruction + rejection criteria */}
                {!batchDone && (
                  <div className="space-y-3 mb-5">
                    <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 flex items-start gap-3">
                      <Users size={16} className="text-blue-500 shrink-0 mt-0.5" />
                      <p className="text-sm text-blue-700">
                        <strong>Review each scientist.</strong> Use <span className="font-semibold">Remove User</span> to exclude a scientist entirely,
                        or <span className="font-semibold">✕</span> on individual rows to drop specific experiments.
                        Only <span className="font-semibold text-amber-700">{pendingCount} pending rows</span> will be promoted to GDS.
                      </p>
                    </div>
                    <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                      <p className="text-xs font-bold text-amber-800 mb-1.5">⚠ When to reject a scientist or row:</p>
                      <ul className="text-xs text-amber-700 space-y-1">
                        <li>• <strong>Signal &lt; 1.0</strong> — below detection threshold, likely instrument noise (check Kim_S: 0.95)</li>
                        <li>• <strong>Signal &gt; 20.0</strong> — saturated detector reading, data unreliable</li>
                        <li>• <strong>Single anomalous well</strong> — remove just that row, keep the rest of the scientist&apos;s data</li>
                        <li>• <strong>Entire scientist excluded</strong> — use Remove User if all their readings are suspect</li>
                      </ul>
                    </div>
                  </div>
                )}

                {/* ── AI Batch Agent Panel ── */}
                <div className="mb-6 bg-white border border-purple-200 rounded-xl overflow-hidden">
                  <div className="flex items-center gap-2 px-4 py-3 bg-purple-50 border-b border-purple-100">
                    <Bot size={15} className="text-purple-600" />
                    <p className="text-sm font-semibold text-purple-900">AI Review Agent</p>
                    <span className="text-xs text-purple-500 ml-1">— acts on all flagged scientists at once</span>
                  </div>
                  <div className="p-4 space-y-3">
                    <textarea
                      value={aiMessage}
                      onChange={e => setAiMessage(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runBatchAgent() } }}
                      placeholder={`e.g. "Remove Chen_L's wells"  or  "Singh_A and Walsh_D don't want their data migrated"  or  "Approve everyone"`}
                      rows={2}
                      disabled={aiRunning}
                      className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-purple-400 placeholder-gray-400 disabled:opacity-50"
                    />
                    <div className="flex items-center justify-between">
                      <p className="text-xs text-gray-400">Press Enter to send · Shift+Enter for new line</p>
                      <button
                        onClick={runBatchAgent}
                        disabled={!aiMessage.trim() || aiRunning}
                        className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white text-sm font-semibold rounded-lg transition disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {aiRunning ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                        {aiRunning ? 'Agent thinking…' : 'Send'}
                      </button>
                    </div>
                    {aiResult && (
                      <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-2">
                        <div className="flex items-center gap-2 mb-1">
                          <CheckCircle2 size={13} className="text-green-500" />
                          <p className="text-xs font-semibold text-gray-700">Agent completed — {aiResult.actions_taken.length} action(s) taken</p>
                        </div>
                        <pre className="text-xs text-gray-600 whitespace-pre-wrap font-sans leading-relaxed">{aiResult.result}</pre>
                      </div>
                    )}
                  </div>
                </div>

                {/* User cards */}
                <div className="space-y-3 mb-6">
                  {Object.entries(grouped).map(([name, rows]) => (
                    <UserCard
                      key={name} name={name}
                      role={rows[0]?.scientist_role ?? ''}
                      rows={rows}
                      traceId={traceId}
                      onRefresh={() => fetchStaging(traceId)}
                    />
                  ))}
                </div>
              </>
            )}
          </div>
        </main>

        {/* HITL Action Bar */}
        {data && (
          <div className="border-t border-gray-200 bg-white px-8 py-4 flex items-center gap-4 flex-shrink-0 shadow-[0_-2px_8px_rgba(0,0,0,0.04)]">
            <div className="flex-1">
              <p className="text-sm font-bold text-gray-900">Ready to commit?</p>
              <p className="text-xs text-gray-400 mt-0.5">
                {batchDone
                  ? 'Batch processed. Open GDS to see the result.'
                  : `${pendingCount} rows from ${pendingUsers} scientists will be promoted. ${excludedCount > 0 ? `${excludedCount} excluded.` : ''}`}
              </p>
            </div>
            <button onClick={handleReject} disabled={action !== 'idle' || batchDone}
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold border-2 border-red-200 text-red-600 bg-red-50 hover:bg-red-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
              {action === 'rejecting' ? <Loader2 size={15} className="animate-spin" /> : <XCircle size={15} />}
              Reject All
            </button>
            <button onClick={handleApprove} disabled={action !== 'idle' || batchDone || pendingCount === 0}
              className="flex items-center gap-2 px-6 py-2.5 rounded-lg text-sm font-bold bg-blue-700 hover:bg-blue-800 text-white shadow-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
              {action === 'approving' ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
              Approve Selected ({pendingCount} rows)
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center bg-gray-50"><Loader2 size={24} className="animate-spin text-blue-500" /></div>}>
      <HITLView />
    </Suspense>
  )
}
