'use client'

import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import {
  FlaskConical, Loader2, AlertTriangle, CheckCircle2, XCircle,
  Check, X, Bot, ShieldCheck, Send,
} from 'lucide-react'

const API = 'http://localhost:8001'

interface Well {
  staging_id:    number
  well_position: string
  signal:        number | null
  risk_level:    'auto' | 'review'
  status:        string
}

interface Context {
  scientist_name: string
  status:         string
  pending_wells:  number
  wells:          Well[]
}

interface Action {
  action:         string
  well_position?: string
  staging_id?:    number
  excluded_count?: number
}

interface AgentResult {
  result:            string
  actions_taken:     Action[]
  invitation_status: string
  pending_remaining: number
}

type Decision = 'keep' | 'drop'
type Phase    = 'loading' | 'error' | 'decide' | 'confirm' | 'submitting' | 'done'

export default function Page() {
  const params = useParams()
  const token  = (params?.token as string) ?? ''

  const [phase,   setPhase]   = useState<Phase>('loading')
  const [ctx,     setCtx]     = useState<Context | null>(null)
  const [errMsg,  setErrMsg]  = useState('')
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [note,    setNote]    = useState('')
  const [result,  setResult]  = useState<AgentResult | null>(null)

  // Load this scientist's flagged wells (token-scoped).
  useEffect(() => {
    if (!token) return
    ;(async () => {
      try {
        const res = await fetch(`${API}/api/respond/${token}`)
        if (!res.ok) {
          const b = await res.json().catch(() => ({}))
          throw new Error(b.detail ?? `HTTP ${res.status}`)
        }
        const data: Context = await res.json()
        setCtx(data)
        // Default every pending well to "keep" — the scientist flips the bad ones.
        const pending = data.wells.filter(w => w.status === 'pending')
        setDecisions(Object.fromEntries(pending.map(w => [w.well_position, 'keep'])))
        setPhase(pending.length ? 'decide' : 'done')
      } catch (e: unknown) {
        setErrMsg(e instanceof Error ? e.message : 'Failed to load')
        setPhase('error')
      }
    })()
  }, [token])

  const pending = ctx?.wells.filter(w => w.status === 'pending') ?? []
  const keeps = Object.entries(decisions).filter(([, d]) => d === 'keep').map(([w]) => w)
  const drops = Object.entries(decisions).filter(([, d]) => d === 'drop').map(([w]) => w)

  const submit = async () => {
    setPhase('submitting')
    try {
      const res = await fetch(`${API}/api/respond/${token}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decisions, note: note.trim() || undefined }),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail ?? `HTTP ${res.status}`)
      }
      setResult(await res.json())
      setPhase('done')
    } catch (e: unknown) {
      setErrMsg(e instanceof Error ? e.message : 'Submit failed')
      setPhase('error')
    }
  }

  // ── Shell ───────────────────────────────────────────────────────────────────
  const Shell = ({ children }: { children: React.ReactNode }) => (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center px-4 py-8">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-2.5 mb-5 px-1">
          <div className="w-8 h-8 bg-blue-700 rounded-lg flex items-center justify-center">
            <FlaskConical size={15} className="text-white" strokeWidth={2.5} />
          </div>
          <div>
            <p className="text-sm font-bold text-gray-900 leading-none">Genomics Data Services</p>
            <p className="text-[11px] text-gray-400 mt-0.5">Secure data review</p>
          </div>
        </div>
        {children}
        <p className="text-center text-[10px] text-gray-300 mt-6">
          This link is personal to you and expires after 7 days.
        </p>
      </div>
    </div>
  )

  const Card = ({ children }: { children: React.ReactNode }) => (
    <div className="bg-white border border-gray-200 rounded-2xl shadow-sm p-5">{children}</div>
  )

  // ── Loading ─────────────────────────────────────────────────────────────────
  if (phase === 'loading') return (
    <Shell><Card>
      <div className="flex flex-col items-center py-10">
        <Loader2 size={22} className="animate-spin text-blue-500 mb-3" />
        <p className="text-sm text-gray-400">Loading your flagged wells…</p>
      </div>
    </Card></Shell>
  )

  // ── Error / expired ─────────────────────────────────────────────────────────
  if (phase === 'error') return (
    <Shell><Card>
      <div className="flex flex-col items-center text-center py-8">
        <AlertTriangle size={28} className="text-amber-400 mb-3" />
        <p className="text-sm font-semibold text-gray-800">We couldn&rsquo;t open this link</p>
        <p className="text-xs text-gray-500 mt-1.5">{errMsg}</p>
        <p className="text-xs text-gray-400 mt-3">Please contact the data migration team for a fresh link.</p>
      </div>
    </Card></Shell>
  )

  // ── Done ────────────────────────────────────────────────────────────────────
  if (phase === 'done') return (
    <Shell><Card>
      <div className="flex flex-col items-center text-center py-6">
        <div className="w-12 h-12 rounded-full bg-emerald-50 flex items-center justify-center mb-3">
          <CheckCircle2 size={26} className="text-emerald-500" />
        </div>
        <p className="text-base font-bold text-gray-900">
          {result ? 'Thank you — your decision is recorded' : 'Nothing left to review'}
        </p>
        <p className="text-xs text-gray-500 mt-1.5">
          {result
            ? 'The migration team has been notified. No further action is needed.'
            : 'All of your flagged wells have already been resolved.'}
        </p>

        {result && (
          <div className="w-full mt-5 text-left">
            {result.actions_taken?.length > 0 && (
              <div className="space-y-1.5 mb-4">
                {result.actions_taken.map((a, i) => (
                  <div key={i} className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg ${
                    a.action === 'approved' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'
                  }`}>
                    {a.action === 'approved'
                      ? <Check size={13} className="shrink-0" />
                      : <X size={13} className="shrink-0" />}
                    {a.action === 'approved'
                      ? `Kept well ${a.well_position ?? `#${a.staging_id}`}`
                      : a.action === 'excluded_all'
                        ? `Dropped all wells (${a.excluded_count ?? 0})`
                        : `Dropped well ${a.well_position ?? `#${a.staging_id}`}`}
                  </div>
                ))}
              </div>
            )}
            {result.result && (
              <div className="bg-gray-900 rounded-xl p-3.5">
                <div className="flex items-center gap-1.5 mb-2">
                  <Bot size={12} className="text-blue-400" />
                  <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                    Reviewed by the migration agent
                  </span>
                </div>
                <p className="text-xs text-gray-100 leading-relaxed whitespace-pre-wrap">{result.result}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </Card></Shell>
  )

  // ── Confirm (client-side review of explicit choices) ────────────────────────
  if (phase === 'confirm' || phase === 'submitting') return (
    <Shell><Card>
      <div className="flex items-center gap-2 mb-4">
        <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center">
          <Bot size={14} className="text-white" />
        </div>
        <p className="text-sm font-bold text-gray-900">Confirm your decision</p>
      </div>

      <div className="space-y-1.5 mb-4">
        {keeps.map(w => (
          <div key={w} className="flex items-center gap-2 text-sm bg-emerald-50 text-emerald-700 px-3 py-2 rounded-lg">
            <Check size={14} /> <span className="font-mono font-semibold">{w}</span>
            <span className="text-emerald-500 text-xs ml-auto">Keep · migrate</span>
          </div>
        ))}
        {drops.map(w => (
          <div key={w} className="flex items-center gap-2 text-sm bg-red-50 text-red-600 px-3 py-2 rounded-lg">
            <X size={14} /> <span className="font-mono font-semibold">{w}</span>
            <span className="text-red-400 text-xs ml-auto">Drop · exclude</span>
          </div>
        ))}
      </div>

      {note.trim() && (
        <div className="text-xs text-gray-600 bg-gray-50 border border-gray-100 rounded-lg px-3 py-2 mb-4">
          <span className="text-gray-400">Reason to keep: </span>{note.trim()}
        </div>
      )}

      <button onClick={submit} disabled={phase === 'submitting'}
        className="w-full flex items-center justify-center gap-2 bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors disabled:opacity-50">
        {phase === 'submitting'
          ? <><Loader2 size={15} className="animate-spin" /> Submitting to the agent…</>
          : <><ShieldCheck size={15} /> Confirm &amp; submit</>}
      </button>
      <button onClick={() => setPhase('decide')} disabled={phase === 'submitting'}
        className="w-full mt-2 text-xs text-gray-400 hover:text-gray-600 py-1.5 disabled:opacity-50">
        ← Edit my choices
      </button>
    </Card></Shell>
  )

  // ── Decide (hybrid: per-well toggle + optional note) ────────────────────────
  return (
    <Shell><Card>
      <p className="text-base font-bold text-gray-900">
        Hi {ctx?.scientist_name} — {pending.length} well{pending.length === 1 ? '' : 's'} need your call
      </p>
      <p className="text-xs text-gray-500 mt-1 mb-4">
        These readings were flagged as anomalous during migration. Tell us which to keep.
      </p>

      <div className="space-y-2 mb-4">
        {pending.map(w => {
          const d = decisions[w.well_position]
          return (
            <div key={w.staging_id} className="flex items-center gap-3 border border-gray-200 rounded-xl px-3 py-2.5">
              <span className="font-mono text-sm font-bold text-gray-800 w-10">{w.well_position}</span>
              <span className="font-mono text-sm text-amber-600 flex-1">
                {w.signal != null ? w.signal.toFixed(3) : '—'}
                <span className="ml-1.5 text-[10px] text-amber-400">⚠ anomalous</span>
              </span>
              <div className="flex rounded-lg overflow-hidden border border-gray-200">
                <button onClick={() => setDecisions(s => ({ ...s, [w.well_position]: 'keep' }))}
                  className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    d === 'keep' ? 'bg-emerald-500 text-white' : 'bg-white text-gray-400 hover:bg-gray-50'
                  }`}>
                  <Check size={12} /> Keep
                </button>
                <button onClick={() => setDecisions(s => ({ ...s, [w.well_position]: 'drop' }))}
                  className={`flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    d === 'drop' ? 'bg-red-500 text-white' : 'bg-white text-gray-400 hover:bg-gray-50'
                  }`}>
                  <X size={12} /> Drop
                </button>
              </div>
            </div>
          )
        })}
      </div>

      <label className="text-xs font-semibold text-gray-500">
        Reason to keep{' '}
        <span className="font-normal text-gray-400">
          {keeps.length ? '(required — documented in the audit trail)' : '(optional)'}
        </span>
      </label>
      <textarea value={note} onChange={e => setNote(e.target.value)} rows={3}
        placeholder={keeps.length
          ? `Why keep ${keeps.join(', ')}? e.g. The spike was real — the instrument was fine that day.`
          : 'Optional note about your decision.'}
        className="w-full mt-1.5 border border-gray-200 rounded-xl px-3 py-2.5 text-sm text-gray-800 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition" />

      {keeps.length > 0 && !note.trim() && (
        <p className="text-[11px] text-amber-600 mt-1.5">
          Keeping a flagged well overrides its anomaly flag — please give a reason so it&rsquo;s on record.
        </p>
      )}

      <button onClick={() => setPhase('confirm')}
        disabled={keeps.length > 0 && !note.trim()}
        className="w-full mt-4 flex items-center justify-center gap-2 bg-blue-700 hover:bg-blue-800 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
        <Send size={14} /> Submit decision
      </button>
    </Card></Shell>
  )
}
