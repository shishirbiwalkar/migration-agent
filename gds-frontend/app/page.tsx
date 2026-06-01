'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import {
  FlaskConical, Users, RefreshCw, Search,
  LayoutDashboard, Database, Activity, Settings,
  ChevronRight, X, Loader2, AlertTriangle, LogOut,
} from 'lucide-react'

const API = 'http://localhost:8001'

// ── Types ──────────────────────────────────────────────────────────────────────

interface GDSUser {
  gds_user_id:      string
  name:             string
  role:             string
  experiment_count: number
  avg_signal:       number | null
  last_import:      string | null
  source?:          string   // 'MIGRATED' | 'NATIVE' — added in future backend update
}

interface WellRecord {
  experiment_id:  string
  trace_id:       string
  well_position:  string
  signal:         number
  approved_at:    string
  approved_by:    string
  compound_id?:   string | null
  concentration?: number | null
  assay_type?:    string | null
}

// ── Stats helpers ─────────────────────────────────────────────────────────────

function calcMean(vals: number[]) {
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

function calcStdDev(vals: number[], avg: number) {
  const variance = vals.reduce((s, v) => s + Math.pow(v - avg, 2), 0) / vals.length
  return Math.sqrt(variance)
}

// ── 96-well heatmap ────────────────────────────────────────────────────────────

const ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const COLS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'))

function wellBg(signal: number, avg: number, sd: number): string {
  const diff = Math.abs(signal - avg)
  if (diff <= sd)     return 'bg-emerald-400 border-emerald-500'
  if (diff <= 2 * sd) return 'bg-yellow-400  border-yellow-500'
  return 'bg-red-500 border-red-600'
}

function WellHeatmap({ wells, avg, sd }: { wells: WellRecord[]; avg: number; sd: number }) {
  const map = new Map(wells.map(w => [w.well_position, w.signal]))

  return (
    <div>
      {/* Column numbers */}
      <div className="flex mb-1 ml-6">
        {COLS.map(c => (
          <div key={c} className="w-8 text-center text-[9px] text-gray-400 font-mono leading-none">
            {parseInt(c)}
          </div>
        ))}
      </div>

      {/* Rows */}
      {ROWS.map(row => (
        <div key={row} className="flex items-center mb-0.5">
          <span className="w-6 text-[10px] text-gray-400 font-mono font-semibold shrink-0">{row}</span>
          {COLS.map(col => {
            const pos = `${row}${col}`
            const sig = map.get(pos)
            return sig == null
              ? (
                <div
                  key={col}
                  title={`${pos} — empty`}
                  className="w-8 h-5 mx-px rounded-sm bg-gray-100 border border-gray-200"
                />
              ) : (
                <div
                  key={col}
                  title={`${pos}: ${sig.toFixed(3)}`}
                  className={`w-8 h-5 mx-px rounded-sm border cursor-default ${wellBg(sig, avg, sd)}`}
                />
              )
          })}
        </div>
      ))}

      {/* Legend */}
      <div className="flex items-center gap-5 mt-3">
        {[
          { cls: 'bg-emerald-400',                        label: '≤ 1σ from mean' },
          { cls: 'bg-yellow-400',                         label: '1σ – 2σ' },
          { cls: 'bg-red-500',                            label: '> 2σ  outlier' },
          { cls: 'bg-gray-100 border border-gray-200',    label: 'empty well' },
        ].map(({ cls, label }) => (
          <div key={label} className="flex items-center gap-1.5">
            <div className={`w-3.5 h-3.5 rounded-sm ${cls}`} />
            <span className="text-[10px] text-gray-400">{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Shared UI pieces ──────────────────────────────────────────────────────────

const DEPT_COLOR: Record<string, string> = {
  'Biochemistry':      'bg-blue-50 text-blue-700',
  'Molecular Biology': 'bg-indigo-50 text-indigo-700',
  'Pharmacology':      'bg-sky-50 text-sky-700',
  'Immunology':        'bg-cyan-50 text-cyan-700',
  'Genomics':          'bg-violet-50 text-violet-700',
  'Proteomics':        'bg-orange-50 text-orange-700',
  'Toxicology':        'bg-slate-100 text-slate-600',
  'Cell Biology':      'bg-emerald-50 text-emerald-700',
}

function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  const initials = name.split('_').map((p: string) => p[0]).join('').slice(0, 2).toUpperCase()
  const cls = { sm: 'w-7 h-7 text-[10px]', md: 'w-9 h-9 text-xs', lg: 'w-11 h-11 text-sm' }[size]
  return (
    <div className={`${cls} rounded-full bg-blue-100 border border-blue-200 flex items-center justify-center text-blue-700 font-bold flex-shrink-0`}>
      {initials}
    </div>
  )
}

function SourceBadge({ source }: { source?: string }) {
  if (source === 'NATIVE')
    return <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">NATIVE</span>
  return <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-blue-50 text-blue-700 border border-blue-200">MIGRATED</span>
}

function NavItem({ icon: Icon, label, active = false }: { icon: React.ElementType; label: string; active?: boolean }) {
  return (
    <button className={`flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left ${
      active ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-100 hover:text-gray-800'
    }`}>
      <Icon size={15} strokeWidth={active ? 2.5 : 1.8} className={active ? 'text-blue-600' : 'text-gray-400'} />
      {label}
    </button>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function GDSAdmin() {
  const router = useRouter()
  const [authed,       setAuthed]       = useState(false)
  const [users,        setUsers]        = useState<GDSUser[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [search,       setSearch]       = useState('')
  const [srcFilter,    setSrcFilter]    = useState<'ALL' | 'MIGRATED' | 'NATIVE'>('ALL')
  const [selected,     setSelected]     = useState<GDSUser | null>(null)
  const [expLoading,   setExpLoading]   = useState(false)
  const [wells,        setWells]        = useState<WellRecord[]>([])

  const fetchUsers = async () => {
    setLoading(true); setError(null)
    try {
      const res = await fetch(`${API}/api/gds/users`)
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const data = await res.json()
      setUsers(data.users)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally { setLoading(false) }
  }

  const fetchWells = async (userId: string) => {
    setExpLoading(true); setWells([])
    try {
      const res = await fetch(`${API}/api/gds/users/${userId}/experiments`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      setWells(data.experiments)
    } catch { setWells([]) }
    finally { setExpLoading(false) }
  }

  // Auth guard — fetch users immediately after auth confirmed
  useEffect(() => {
    if (localStorage.getItem('gds_auth') === 'true') {
      setAuthed(true)
      fetchUsers()
    } else {
      router.replace('/login')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router])

  const logout = () => {
    localStorage.removeItem('gds_auth')
    router.replace('/login')
  }

  const selectUser = (u: GDSUser) => { setSelected(u); fetchWells(u.gds_user_id) }

  // Derived counts for summary cards
  const migratedCount = users.filter(u => (u.source ?? 'MIGRATED') !== 'NATIVE').length
  const nativeCount   = users.filter(u => u.source === 'NATIVE').length

  // Filtered list
  const filtered = users.filter(u => {
    const matchSearch  = !search || u.name.toLowerCase().includes(search.toLowerCase()) || u.role.toLowerCase().includes(search.toLowerCase())
    const matchSource  = srcFilter === 'ALL' || (u.source ?? 'MIGRATED') === srcFilter
    return matchSearch && matchSource
  })

  // Per-scientist well stats
  const signals      = wells.map(w => w.signal)
  const wellMean     = signals.length ? calcMean(signals) : 0
  const wellSd       = signals.length > 1 ? calcStdDev(signals, wellMean) : 0
  const outlierCount = wells.filter(w => Math.abs(w.signal - wellMean) > 2 * wellSd).length

  if (!authed) return null

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>

      {/* ── Sidebar ── */}
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col flex-shrink-0">
        <div className="px-5 py-5 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-blue-700 rounded-lg flex items-center justify-center">
              <FlaskConical size={14} className="text-white" strokeWidth={2.5} />
            </div>
            <div>
              <p className="text-sm font-bold text-gray-900 leading-none">GD Screener</p>
              <p className="text-[10px] text-gray-400 mt-0.5">Target Platform</p>
            </div>
          </div>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-0.5">
          <NavItem icon={LayoutDashboard} label="Dashboard" />
          <NavItem icon={Users}           label="Scientists" active />
          <NavItem icon={Database}        label="Experiments" />
          <NavItem icon={Activity}        label="System Health" />
          <NavItem icon={Settings}        label="Settings" />
        </nav>
        <div className="px-4 py-4 border-t border-gray-100 space-y-2">
          <div className="bg-blue-50 rounded-lg px-3 py-2.5 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
            <div>
              <p className="text-[10px] font-bold text-blue-700 uppercase tracking-wider">GDS-PROD</p>
              <p className="text-xs text-blue-600 font-medium">Live · Connected</p>
            </div>
          </div>
          <button
            onClick={logout}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors"
          >
            <LogOut size={13} />
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Right of sidebar ── */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* ── Summary cards strip ── */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
          <div className="grid grid-cols-4 gap-4">
            {[
              { label: 'Total Scientists', value: users.length,    color: 'text-blue-700',    bg: 'bg-blue-50',    border: 'border-blue-100'    },
              { label: 'Migrated',         value: migratedCount,   color: 'text-indigo-700',  bg: 'bg-indigo-50',  border: 'border-indigo-100'  },
              { label: 'Native GDS',       value: nativeCount,     color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-100' },
              { label: 'Flagged Wells',    value: 0,               color: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-100'   },
            ].map(({ label, value, color, bg, border }) => (
              <div key={label} className={`rounded-xl border ${border} ${bg} px-4 py-3`}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-500 mb-1">{label}</p>
                <p className={`text-2xl font-bold ${color}`}>{loading ? '—' : value}</p>
              </div>
            ))}
          </div>
        </div>

        {/* ── List + Detail ── */}
        <div className="flex flex-1 min-h-0 overflow-hidden">

          {/* ── Scientist list ── */}
          <div className="w-80 flex flex-col border-r border-gray-200 bg-white flex-shrink-0">

            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-gray-900">Scientists</h2>
                <p className="text-xs text-gray-400 mt-0.5">{filtered.length} shown · {users.length} total</p>
              </div>
              <button onClick={fetchUsers} className="p-1.5 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-colors">
                <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              </button>
            </div>

            {/* Search + source filter */}
            <div className="px-4 py-3 border-b border-gray-100 space-y-2">
              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text" placeholder="Search name or dept…" value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg bg-white text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="flex gap-1">
                {(['ALL', 'MIGRATED', 'NATIVE'] as const).map(s => (
                  <button key={s} onClick={() => setSrcFilter(s)}
                    className={`flex-1 text-[10px] font-semibold py-1.5 rounded-lg transition-colors ${
                      srcFilter === s
                        ? s === 'NATIVE'    ? 'bg-emerald-600 text-white'
                        : s === 'MIGRATED'  ? 'bg-blue-600 text-white'
                        : 'bg-gray-800 text-white'
                        : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                    }`}>
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex-1 overflow-y-auto">
              {loading && (
                <div className="flex flex-col items-center py-16 text-gray-400">
                  <Loader2 size={20} className="animate-spin text-blue-400 mb-2" />
                  <p className="text-xs">Loading…</p>
                </div>
              )}
              {error && (
                <div className="m-4 bg-red-50 border border-red-200 rounded-lg p-3 flex gap-2">
                  <AlertTriangle size={14} className="text-red-500 shrink-0 mt-0.5" />
                  <p className="text-xs text-red-600">{error}</p>
                </div>
              )}
              {!loading && !error && users.length === 0 && (
                <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
                  <Users size={28} className="text-gray-300 mb-3" />
                  <p className="text-sm font-medium text-gray-500 mb-1">No scientists yet</p>
                  <p className="text-xs text-gray-400">Run the AI agent in ABASE, then approve in HITL review.</p>
                </div>
              )}
              {filtered.map(u => (
                <button key={u.gds_user_id} onClick={() => selectUser(u)}
                  className={`w-full text-left px-4 py-3 border-b border-gray-50 hover:bg-gray-50 transition-colors flex items-center gap-3 ${selected?.gds_user_id === u.gds_user_id ? 'bg-blue-50' : ''}`}>
                  <Avatar name={u.name} size="sm" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-gray-900 truncate">{u.name}</p>
                    <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                      <SourceBadge source={u.source} />
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${DEPT_COLOR[u.role] ?? 'bg-gray-100 text-gray-600'}`}>
                        {u.role}
                      </span>
                      {u.experiment_count > 0 && (
                        <span className="text-[10px] text-gray-400">{u.experiment_count}w · avg {u.avg_signal?.toFixed(2)}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRight size={14} className="text-gray-300 shrink-0" />
                </button>
              ))}
            </div>
          </div>

          {/* ── Detail panel ── */}
          <div className="flex-1 overflow-y-auto flex flex-col">
            {selected ? (
              <div className="p-6 max-w-4xl w-full">

                {/* Header */}
                <div className="flex items-start justify-between mb-5">
                  <div className="flex items-center gap-4">
                    <Avatar name={selected.name} size="lg" />
                    <div>
                      <h1 className="text-xl font-bold text-gray-900">{selected.name}</h1>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <SourceBadge source={selected.source} />
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold ${DEPT_COLOR[selected.role] ?? 'bg-gray-100 text-gray-600'}`}>
                          {selected.role}
                        </span>
                        {selected.avg_signal != null && (
                          <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full border border-blue-100 font-semibold">
                            avg signal: {selected.avg_signal.toFixed(3)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <button onClick={() => { setSelected(null); setWells([]) }} className="text-gray-400 hover:text-gray-600 p-1">
                    <X size={18} />
                  </button>
                </div>

                {/* Provenance banner */}
                {!expLoading && wells.length > 0 && (() => {
                  const isNative = selected.source === 'NATIVE'
                  return (
                    <div className={`rounded-xl border px-4 py-3 mb-5 flex items-start gap-3 ${isNative ? 'bg-emerald-50 border-emerald-200' : 'bg-blue-50 border-blue-200'}`}>
                      <div className={`w-1 self-stretch rounded-full flex-shrink-0 ${isNative ? 'bg-emerald-400' : 'bg-blue-400'}`} />
                      <div className="min-w-0">
                        {isNative ? (
                          <>
                            <p className="text-xs font-bold text-emerald-800">Native GDS Record</p>
                            <p className="text-[11px] text-emerald-600 mt-0.5">
                              Data loaded directly into GDS — not migrated from ABASE.
                              {wells[0]?.approved_by && <> · Approved by: <span className="font-semibold">{wells[0].approved_by}</span></>}
                            </p>
                          </>
                        ) : (
                          <>
                            <p className="text-xs font-bold text-blue-800">Migrated from ABASE</p>
                            <p className="text-[11px] text-blue-600 mt-0.5 break-all">
                              {wells[0]?.approved_by && <>Approved by: <span className="font-semibold">{wells[0].approved_by}</span></>}
                              {wells[0]?.approved_at && <> · on {new Date(wells[0].approved_at).toLocaleDateString()}</>}
                              {wells[0]?.trace_id && <> · <span className="font-mono text-blue-400">Trace: {wells[0].trace_id}</span></>}
                            </p>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })()}

                {expLoading ? (
                  <div className="flex items-center justify-center py-20">
                    <Loader2 size={22} className="animate-spin text-blue-400" />
                  </div>
                ) : wells.length === 0 ? (
                  <div className="text-center py-16 text-sm text-gray-400">No well records found.</div>
                ) : (
                  <>
                    {/* Signal analytics — 6 stat cards */}
                    <div className="grid grid-cols-3 gap-3 mb-6">
                      {[
                        { label: 'Total Wells',   value: wells.length,                        isAlert: false },
                        { label: 'Mean Signal',   value: wellMean.toFixed(3),                  isAlert: false },
                        { label: 'Std Dev  (σ)',  value: wellSd.toFixed(3),                    isAlert: false },
                        { label: 'Min Signal',    value: Math.min(...signals).toFixed(3),       isAlert: false },
                        { label: 'Max Signal',    value: Math.max(...signals).toFixed(3),       isAlert: false },
                        { label: 'Outliers >2σ',  value: outlierCount,                         isAlert: outlierCount > 0 },
                      ].map(({ label, value, isAlert }) => (
                        <div key={label} className="bg-white border border-gray-200 rounded-xl px-4 py-3 shadow-sm">
                          <p className="text-[10px] uppercase tracking-widest text-gray-400 font-semibold mb-1">{label}</p>
                          <p className={`text-xl font-bold ${isAlert ? 'text-red-600' : 'text-blue-700'}`}>{value}</p>
                        </div>
                      ))}
                    </div>

                    {/* 96-well heatmap */}
                    <div className="bg-white border border-gray-200 rounded-xl p-5 mb-6 shadow-sm overflow-x-auto">
                      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-4">
                        96-Well Plate Heatmap
                      </h3>
                      {wellSd > 0
                        ? <WellHeatmap wells={wells} avg={wellMean} sd={wellSd} />
                        : <p className="text-xs text-gray-400">Not enough wells to compute σ.</p>
                      }
                    </div>

                    {/* Well data table */}
                    <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
                      <div className="px-5 py-3 bg-gray-50 border-b border-gray-100 flex items-center justify-between">
                        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Well Records</h3>
                        <span className="text-xs text-blue-500 font-mono">
                          {selected.source === 'NATIVE' ? 'Native GDS' : 'ABASE → AI Agent → GDS'}
                        </span>
                      </div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="border-b border-gray-100">
                              {['Well', 'Signal', 'Quality', 'Compound ID', 'Conc (µM)', 'Assay Type'].map(h => (
                                <th key={h} className="text-left px-4 py-2.5 text-[11px] font-semibold text-gray-400 uppercase tracking-wider whitespace-nowrap">{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-50">
                            {wells.map(w => {
                              const diff = Math.abs(w.signal - wellMean)
                              const qualityBadge =
                                diff > 2 * wellSd
                                  ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-100    text-red-700    font-semibold">Outlier</span>
                                  : diff > wellSd
                                  ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700 font-semibold">Review</span>
                                  : <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-semibold">Normal</span>
                              return (
                                <tr key={w.experiment_id} className="hover:bg-blue-50/30 transition-colors">
                                  <td className="px-4 py-2.5 font-mono font-bold text-gray-800">{w.well_position}</td>
                                  <td className="px-4 py-2.5 font-mono font-semibold text-blue-700">{w.signal.toFixed(4)}</td>
                                  <td className="px-4 py-2.5">{qualityBadge}</td>
                                  <td className="px-4 py-2.5 text-xs text-gray-500">{w.compound_id      ?? '—'}</td>
                                  <td className="px-4 py-2.5 text-xs text-gray-500">{w.concentration != null ? w.concentration.toFixed(1) : '—'}</td>
                                  <td className="px-4 py-2.5 text-xs text-gray-500">{w.assay_type       ?? '—'}</td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ) : (
              /* Empty state */
              <div className="flex-1 flex flex-col items-center justify-center text-center px-8">
                <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mx-auto mb-5">
                  <FlaskConical size={28} className="text-blue-400" />
                </div>
                <p className="text-base font-semibold text-gray-700 mb-2">
                  {users.length === 0 ? 'No scientists migrated yet' : 'Select a scientist'}
                </p>
                <p className="text-sm text-gray-400 max-w-xs">
                  {users.length === 0
                    ? 'Run the AI agent in ABASE → approve in HITL → data appears here'
                    : 'Click a scientist to view their well heatmap, signal analytics, and records'}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
