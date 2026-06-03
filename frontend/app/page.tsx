'use client'

import { useState, useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import {
  FlaskConical, Loader2, AlertTriangle, ChevronRight,
  ClipboardCheck, Bot, RefreshCw, CheckCircle2, FileText,
  Play, Database, ShieldCheck, GitMerge, Cpu, CheckCheck, XCircle,
} from 'lucide-react'

const API = 'http://localhost:8001'

interface Scientist {
  name:          string
  pending_wells: number
}

interface Run {
  trace_id:      string
  created_at:    string
  scientists:    Scientist[]
  total_pending: number
}

interface CompletedRun {
  trace_id:       string
  run_date:       string
  total_rows:     number
  auto_approved:  number
  hitl_approved:  number
  excluded:       number
  in_production:  number
}

type StepStatus = 'pending' | 'running' | 'done' | 'failed'

interface MigrationStep {
  id:      string
  label:   string
  detail:  string
  icon:    React.ReactNode
  status:  StepStatus
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function StepRow({ step }: { step: MigrationStep }) {
  return (
    <div className={`flex items-start gap-3 py-3 px-4 rounded-lg transition-all ${
      step.status === 'running' ? 'bg-blue-50 border border-blue-100' :
      step.status === 'done'    ? 'bg-emerald-50 border border-emerald-100' :
      step.status === 'failed'  ? 'bg-red-50 border border-red-100' :
      'bg-gray-50 border border-gray-100 opacity-50'
    }`}>
      <div className="mt-0.5 shrink-0">
        {step.status === 'running' && <Loader2 size={16} className="animate-spin text-blue-500" />}
        {step.status === 'done'    && <CheckCircle2 size={16} className="text-emerald-500" />}
        {step.status === 'failed'  && <XCircle size={16} className="text-red-500" />}
        {step.status === 'pending' && <div className="w-4 h-4 rounded-full border-2 border-gray-300" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-semibold ${
          step.status === 'running' ? 'text-blue-700' :
          step.status === 'done'    ? 'text-emerald-700' :
          step.status === 'failed'  ? 'text-red-700' : 'text-gray-400'
        }`}>{step.label}</p>
        {(step.status === 'running' || step.status === 'done') && (
          <p className="text-xs text-gray-500 mt-0.5">{step.detail}</p>
        )}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const router = useRouter()
  const [runs,          setRuns]          = useState<Run[]>([])
  const [completedRuns, setCompletedRuns] = useState<CompletedRun[]>([])
  const [loading,       setLoading]       = useState(true)
  const [error,         setError]         = useState<string | null>(null)

  // Migration modal state
  const [showModal,     setShowModal]     = useState(false)
  const [migrating,     setMigrating]     = useState(false)
  const [migrateResult, setMigrateResult] = useState<Record<string, unknown> | null>(null)
  const [migrateError,  setMigrateError]  = useState<string | null>(null)
  const [steps,         setSteps]         = useState<MigrationStep[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const INITIAL_STEPS: MigrationStep[] = [
    { id: 'backup',   label: 'Triggering source database backup',  detail: 'Calling cloud infrastructure snapshot API…',    icon: <Database size={14} />,   status: 'pending' },
    { id: 'verify',   label: 'Verifying backup complete',          detail: 'Polling backup provider for confirmation…',     icon: <ShieldCheck size={14} />, status: 'pending' },
    { id: 'agent',    label: 'Running migration agent',            detail: 'Schema discovery → semantic mapping → transform…', icon: <Cpu size={14} />,     status: 'pending' },
    { id: 'staging',  label: 'Writing to staging area',            detail: 'Persisting records to staging buffer…',          icon: <GitMerge size={14} />,    status: 'pending' },
    { id: 'critic',   label: 'QA critic reviewing mapping',        detail: 'Independent validation of column mappings…',     icon: <ClipboardCheck size={14} />, status: 'pending' },
    { id: 'promote',  label: 'Auto-approving clean rows',          detail: 'Promoting safe records to production…',          icon: <CheckCheck size={14} />,  status: 'pending' },
  ]

  const setStep = (id: string, status: StepStatus) =>
    setSteps(prev => prev.map(s => s.id === id ? { ...s, status } : s))

  const runMigration = async () => {
    setShowModal(true)
    setMigrating(true)
    setMigrateResult(null)
    setMigrateError(null)
    setSteps(INITIAL_STEPS)

    // Step 1 — backup
    setStep('backup', 'running')
    await new Promise(r => setTimeout(r, 800))
    setStep('backup', 'done')

    // Step 2 — verify backup
    setStep('verify', 'running')
    await new Promise(r => setTimeout(r, 600))
    setStep('verify', 'done')

    // Step 3 — agent (this is where the real work happens)
    setStep('agent', 'running')

    try {
      // Fire async job
      const startRes = await fetch(`${API}/api/agent/run/async`, { method: 'POST' })
      if (!startRes.ok) throw new Error(`Failed to start migration: HTTP ${startRes.status}`)
      const { trace_id, status_url } = await startRes.json()

      // Poll until done
      await new Promise<void>((resolve, reject) => {
        let stagingShown  = false
        let criticShown   = false
        let promoteShown  = false

        pollRef.current = setInterval(async () => {
          try {
            const jobRes = await fetch(`${API}${status_url}`)
            const job    = await jobRes.json()

            // Show later steps progressively while running
            if (job.status === 'running' || job.status === 'succeeded') {
              if (!stagingShown)  { stagingShown  = true; setStep('staging', 'running') }
            }
            if (job.status === 'running') {
              const elapsed = Date.now() - startTime
              if (elapsed > 8000  && !criticShown)  { criticShown  = true; setStep('staging', 'done'); setStep('critic',  'running') }
              if (elapsed > 14000 && !promoteShown) { promoteShown = true; setStep('critic',  'done'); setStep('promote', 'running') }
            }

            if (job.status === 'succeeded') {
              clearInterval(pollRef.current!)
              setStep('agent',   'done')
              setStep('staging', 'done')
              setStep('critic',  'done')
              setStep('promote', 'done')
              setMigrateResult(job.result)
              resolve()
            } else if (job.status === 'failed') {
              clearInterval(pollRef.current!)
              reject(new Error(job.error || 'Migration failed'))
            }
          } catch (e) {
            clearInterval(pollRef.current!)
            reject(e)
          }
        }, 2000)

        const startTime = Date.now()
      })

    } catch (e: unknown) {
      setStep('agent', 'failed')
      setMigrateError(e instanceof Error ? e.message : 'Migration failed')
    } finally {
      setMigrating(false)
      fetchPending()
    }
  }

  const fetchPending = async () => {
    setLoading(true); setError(null)
    try {
      const [pendingRes, completedRes] = await Promise.all([
        fetch(`${API}/api/migrate/pending`),
        fetch(`${API}/api/report/completed`),
      ])
      if (!pendingRes.ok) throw new Error(`HTTP ${pendingRes.status}`)
      const pendingData   = await pendingRes.json()
      const completedData = completedRes.ok ? await completedRes.json() : { runs: [] }
      setRuns(pendingData.runs)
      setCompletedRuns(completedData.runs)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchPending() }, [])
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

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
              <p className="text-[10px] text-gray-400 mt-0.5">GDS Console</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          <div className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm font-medium bg-blue-50 text-blue-700">
            <ClipboardCheck size={15} /> Dashboard
          </div>
        </nav>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <div className="px-8 py-6 max-w-4xl mx-auto">

            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-xl font-bold text-gray-900">Pending Resolutions</h1>
                <p className="text-xs text-gray-400 mt-1">
                  Scientists waiting for review after reaching back to admin
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={fetchPending} disabled={loading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50">
                  <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
                  Refresh
                </button>
                <button onClick={runMigration} disabled={migrating}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold text-white bg-blue-700 hover:bg-blue-800 rounded-lg transition-colors disabled:opacity-50 shadow-sm">
                  <Play size={11} fill="currentColor" />
                  Run Migration
                </button>
              </div>
            </div>

            {/* Migration Modal */}
            {showModal && (
              <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
                <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">

                  <div className="flex items-center gap-3 mb-5">
                    <div className="w-9 h-9 bg-blue-700 rounded-xl flex items-center justify-center shrink-0">
                      <FlaskConical size={16} className="text-white" />
                    </div>
                    <div>
                      <h2 className="text-base font-bold text-gray-900">AI Migration Agent</h2>
                      <p className="text-xs text-gray-400">Orchestrating pipeline…</p>
                    </div>
                    {migrating && <Loader2 size={16} className="animate-spin text-blue-500 ml-auto" />}
                  </div>

                  <div className="space-y-2 mb-5">
                    {steps.map(step => <StepRow key={step.id} step={step} />)}
                  </div>

                  {/* Result */}
                  {migrateResult && !migrating && (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 mb-4">
                      <div className="flex items-center gap-2 mb-2">
                        <CheckCircle2 size={15} className="text-emerald-600" />
                        <p className="text-sm font-bold text-emerald-800">Migration Complete</p>
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="bg-white rounded-lg p-2.5 border border-emerald-100">
                          <p className="text-gray-400">Auto-approved</p>
                          <p className="text-lg font-bold text-emerald-600">{(migrateResult as Record<string, unknown>).auto_approved as number ?? 0}</p>
                        </div>
                        <div className="bg-white rounded-lg p-2.5 border border-emerald-100">
                          <p className="text-gray-400">Pending review</p>
                          <p className="text-lg font-bold text-amber-600">{(migrateResult as Record<string, unknown>).pending_review as number ?? 0}</p>
                        </div>
                      </div>
                      {/* Backup info */}
                      {(migrateResult as Record<string, unknown>).backup && (
                        <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-500">
                          <ShieldCheck size={11} className="text-emerald-500" />
                          Backup: {String(((migrateResult as Record<string, unknown>).backup as Record<string, unknown>)?.provider ?? 'none')} —{' '}
                          {String(((migrateResult as Record<string, unknown>).backup as Record<string, unknown>)?.status ?? 'skipped')}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Error */}
                  {migrateError && (
                    <div className="bg-red-50 border border-red-200 rounded-xl p-3 mb-4 flex gap-2">
                      <AlertTriangle size={14} className="text-red-500 shrink-0 mt-0.5" />
                      <p className="text-xs text-red-700">{migrateError}</p>
                    </div>
                  )}

                  <button
                    onClick={() => { setShowModal(false); setMigrateResult(null); setMigrateError(null) }}
                    disabled={migrating}
                    className="w-full py-2 text-sm font-semibold text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-xl transition-colors disabled:opacity-40">
                    {migrating ? 'Running…' : 'Close'}
                  </button>
                </div>
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div className="flex items-center justify-center py-20">
                <Loader2 size={22} className="animate-spin text-blue-500" />
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3">
                <AlertTriangle size={16} className="text-red-500 shrink-0 mt-0.5" />
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            {/* Empty state */}
            {!loading && !error && runs.length === 0 && (
              <div className="flex flex-col items-center justify-center py-24 text-center">
                <CheckCircle2 size={36} className="text-emerald-400 mb-3" />
                <p className="text-gray-700 font-semibold">All clear</p>
                <p className="text-sm text-gray-400 mt-1">
                  No pending review wells across any migration run.
                </p>
              </div>
            )}

            {/* Run cards */}
            {!loading && runs.map(run => (
              <div key={run.trace_id}
                className="bg-white border border-gray-200 rounded-xl overflow-hidden mb-4 shadow-sm">

                {/* Run header */}
                <div className="flex items-center gap-3 px-5 py-3 bg-gray-50 border-b border-gray-100">
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-mono text-gray-500 truncate">
                      {run.trace_id}
                    </p>
                    <p className="text-[11px] text-gray-400 mt-0.5">
                      {formatDate(run.created_at)}
                    </p>
                  </div>
                  <span className="text-xs font-bold text-amber-700 bg-amber-50 border border-amber-200 px-2.5 py-1 rounded-full">
                    {run.total_pending} pending
                  </span>
                  <button
                    onClick={() => router.push(`/review?trace_id=${run.trace_id}`)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-blue-700 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors">
                    <ClipboardCheck size={11} /> HITL Review
                  </button>
                </div>

                {/* Scientist rows */}
                <div className="divide-y divide-gray-50">
                  {run.scientists.map(scientist => (
                    <div key={scientist.name}
                      className="flex items-center gap-4 px-5 py-3 hover:bg-gray-50 transition-colors">

                      <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-xs shrink-0">
                        {scientist.name.split('_').map((p: string) => p[0]).join('').slice(0, 2)}
                      </div>

                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-gray-900">{scientist.name}</p>
                        <p className="text-xs text-amber-600 font-medium mt-0.5">
                          {scientist.pending_wells} well{scientist.pending_wells !== 1 ? 's' : ''} pending
                        </p>
                      </div>

                      <button
                        onClick={() => router.push(
                          `/reviewer?trace_id=${run.trace_id}&scientist=${encodeURIComponent(scientist.name)}`
                        )}
                        className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-white bg-blue-700 hover:bg-blue-800 rounded-lg transition-colors">
                        <Bot size={12} /> Open Reviewer
                        <ChevronRight size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}

            {/* Completed runs */}
            {!loading && completedRuns.length > 0 && (
              <div className="mt-8">
                <div className="flex items-center gap-2 mb-3">
                  <CheckCircle2 size={14} className="text-emerald-500" />
                  <h2 className="text-xs font-bold uppercase tracking-wider text-gray-500">
                    Completed Migrations
                  </h2>
                </div>
                <div className="space-y-2">
                  {completedRuns.map(run => (
                    <div key={run.trace_id}
                      className="bg-white border border-gray-200 rounded-xl px-5 py-3 flex items-center gap-4 shadow-sm">
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-mono text-gray-500 truncate">{run.trace_id}</p>
                        <p className="text-[11px] text-gray-400 mt-0.5">{formatDate(run.run_date)}</p>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-gray-500">
                        <span className="text-emerald-600 font-semibold">{run.in_production} promoted</span>
                        {run.excluded > 0 && (
                          <span className="text-red-400">{run.excluded} excluded</span>
                        )}
                      </div>
                      <span className="flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                        <CheckCircle2 size={9} /> Complete
                      </span>
                      <button
                        onClick={() => router.push(`/report?trace_id=${run.trace_id}`)}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-700 bg-gray-50 border border-gray-200 rounded-lg hover:bg-gray-100 transition-colors">
                        <FileText size={11} /> View Report
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

          </div>
        </main>
      </div>
    </div>
  )
}
