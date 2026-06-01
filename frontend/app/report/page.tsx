'use client'

import { useState, useEffect, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import {
  FlaskConical, ArrowLeft, Loader2, AlertTriangle,
  CheckCircle2, Copy, Check, FileText,
} from 'lucide-react'

const API = 'http://localhost:8001'

interface ReportData {
  trace_id:     string
  report:       string
  generated_at: string
  data: {
    counts: {
      total_staged:   number
      auto_approved:  number
      review_flagged: number
      hitl_approved:  number
      excluded:       number
      pending:        number
      in_production:  number
    }
  }
}

function ReportView() {
  const searchParams  = useSearchParams()
  const router        = useRouter()
  const traceId       = searchParams.get('trace_id') ?? ''

  const [report,   setReport]   = useState<ReportData | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [copied,   setCopied]   = useState(false)

  const generate = async () => {
    if (!traceId) return
    setLoading(true); setError(null); setReport(null)
    try {
      const res = await fetch(`${API}/api/report/${traceId}`, { method: 'POST' })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail ?? `HTTP ${res.status}`)
      }
      setReport(await res.json())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to generate report')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { if (traceId) generate() }, [traceId])

  const copy = async () => {
    if (!report?.report) return
    await navigator.clipboard.writeText(report.report)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const isPassed = report?.report?.includes('Overall: PASS')

  if (!traceId) return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <AlertTriangle size={32} className="text-amber-400 mx-auto mb-3" />
        <p className="text-gray-600 font-medium">No trace_id in URL.</p>
        <p className="text-xs text-gray-400 mt-1">Expected: /report?trace_id=...</p>
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
              <p className="text-[10px] text-gray-400 mt-0.5">Verification Report</p>
            </div>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          <button onClick={() => router.push('/')}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm text-gray-500 hover:bg-gray-100 transition-colors">
            <ArrowLeft size={14} /> Dashboard
          </button>
          <div className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm font-medium bg-blue-50 text-blue-700">
            <FileText size={15} /> Report
          </div>
        </nav>

        {/* Stats */}
        {report && (
          <div className="px-4 py-4 border-t border-gray-100 space-y-2">
            {[
              { label: 'Staged',      value: report.data.counts.total_staged },
              { label: 'Production',  value: report.data.counts.in_production },
              { label: 'Excluded',    value: report.data.counts.excluded },
              { label: 'Pending',     value: report.data.counts.pending,
                alert: report.data.counts.pending > 0 },
            ].map(({ label, value, alert }) => (
              <div key={label} className="flex justify-between text-xs">
                <span className="text-gray-500">{label}</span>
                <span className={`font-bold ${alert ? 'text-red-500' : 'text-gray-800'}`}>
                  {value}
                </span>
              </div>
            ))}

            {/* Verdict chip */}
            <div className={`mt-3 flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-bold ${
              isPassed
                ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                : 'bg-red-50 text-red-600 border border-red-200'
            }`}>
              {isPassed
                ? <><CheckCircle2 size={12} /> PASS</>
                : <><AlertTriangle size={12} /> FAIL</>}
            </div>
          </div>
        )}

        <div className="px-4 pb-4">
          <div className="bg-gray-50 border border-gray-200 rounded-lg px-2 py-1.5">
            <p className="text-[10px] font-mono text-gray-400 break-all">{traceId.slice(0,20)}…</p>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main className="flex-1 overflow-y-auto">
          <div className="px-8 py-6 max-w-4xl mx-auto">

            {/* Header */}
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-xl font-bold text-gray-900">Verification Report</h1>
                <p className="text-xs text-gray-400 font-mono mt-1">{traceId}</p>
              </div>
              <div className="flex items-center gap-2">
                {report && (
                  <button onClick={copy}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                    {copied
                      ? <><Check size={12} className="text-emerald-500" /> Copied</>
                      : <><Copy size={12} /> Copy</>}
                  </button>
                )}
                <button onClick={generate} disabled={loading}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-blue-700 border border-blue-200 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors disabled:opacity-50">
                  {loading
                    ? <><Loader2 size={11} className="animate-spin" /> Generating…</>
                    : <><FileText size={11} /> Regenerate</>}
                </button>
              </div>
            </div>

            {/* Loading */}
            {loading && (
              <div className="flex flex-col items-center py-24">
                <Loader2 size={24} className="animate-spin text-blue-500 mb-3" />
                <p className="text-sm text-gray-500 font-medium">Generating verification report…</p>
                <p className="text-xs text-gray-400 mt-1">Gemini is analyzing migration evidence</p>
              </div>
            )}

            {/* Error */}
            {error && !loading && (
              <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3">
                <AlertTriangle size={16} className="text-red-500 shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-semibold text-red-700">Failed to generate report</p>
                  <p className="text-xs text-red-500 mt-1">{error}</p>
                </div>
              </div>
            )}

            {/* Report */}
            {report && !loading && (
              <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                <div className="flex items-center justify-between px-5 py-3 bg-gray-50 border-b border-gray-100">
                  <div className="flex items-center gap-2">
                    <FileText size={14} className="text-gray-500" />
                    <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
                      Verification Report
                    </span>
                  </div>
                  <span className="text-[10px] text-gray-400">
                    Generated {new Date(report.generated_at).toLocaleString()}
                  </span>
                </div>
                <pre className="p-6 text-xs leading-relaxed text-gray-800 whitespace-pre-wrap overflow-x-auto"
                  style={{ fontFamily: "'Courier New', Courier, monospace" }}>
                  {report.report}
                </pre>
              </div>
            )}

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
      <ReportView />
    </Suspense>
  )
}
