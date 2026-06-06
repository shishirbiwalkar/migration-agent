'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  FlaskConical, Users, RefreshCw, Search,
  LayoutDashboard, Database, Activity, Settings,
  ChevronRight, X, Loader2, AlertTriangle, LogOut,
  TrendingUp, FlaskRound,
} from 'lucide-react'

const API = 'http://localhost:8001'

// ── Types ──────────────────────────────────────────────────────────────────────

interface GDSUser {
  gds_user_id:    string
  name:           string
  role:           string
  experiment_count: number
  avg_ec50:       number | null
  avg_r_squared:  number | null
  last_import:    string | null
}

interface CurvePoint {
  well_position: string
  conc_um:       number
  response:      number
  quality:       'valid' | 'masked' | 'critical'
}

interface CompoundExperiment {
  experiment_id:            string
  compound_id:              string
  assay_type:               string | null
  ec50_um:                  number | null
  hill_slope:               number | null
  r_squared:                number | null
  curve_quality:            string | null
  num_concentration_points: number | null
  signal:                   number | null
  plate_barcode:            string | null
  curve_data:               CurvePoint[]
  neg_ctrl_mean:            number | null
  pos_ctrl_mean:            number | null
  approved_at:              string
  approved_by:              string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const COLS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'))

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

function responseBg(response: number, quality: string): string {
  if (quality === 'critical') return 'bg-red-200 border-red-400 ring-1 ring-red-400'
  if (quality === 'ctrl_neg') return 'bg-slate-300 border-slate-400 ring-1 ring-slate-400'
  if (quality === 'ctrl_pos') return 'bg-violet-200 border-violet-400 ring-1 ring-violet-400'
  if (response < 5)  return 'bg-sky-100 border-sky-200'
  if (response < 20) return 'bg-emerald-200 border-emerald-300'
  if (response < 45) return 'bg-yellow-300 border-yellow-400'
  if (response < 70) return 'bg-orange-400 border-orange-500'
  if (response < 90) return 'bg-red-500 border-red-600'
  return 'bg-red-800 border-red-900'
}

function concLabel(um: number): string {
  if (um < 0.001) return `${(um * 1000).toFixed(1)} pM`
  if (um < 1)     return `${(um * 1000).toFixed(um < 0.01 ? 1 : 0)} nM`
  if (um < 1000)  return `${um % 1 === 0 ? um : um.toFixed(1)} µM`
  return `${(um / 1000).toFixed(1)} mM`
}

// ── 96-Well Plate Heatmap — all scientists on one plate ──────────────────────

interface PlateWell {
  scientist_name: string
  compound_id:    string
  well_position:  string
  conc_um:        number
  response:       number
  quality:        'valid' | 'masked' | 'critical' | 'ctrl_neg' | 'ctrl_pos'
  rfu_mean?:      number
}

function PlateHeatmap({
  wells,
  highlightScientist,
  plateBarcode,
}: {
  wells: PlateWell[]
  highlightScientist: string
  plateBarcode: string
}) {
  const wellMap = new Map<string, PlateWell>()
  for (const w of wells) wellMap.set(w.well_position, w)

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          96-Well Plate — {plateBarcode}
        </h3>
        <div className="flex items-center gap-3 text-[10px] text-gray-400">
          {[
            { cls: 'bg-sky-100 border-sky-200',         label: '<5%'      },
            { cls: 'bg-emerald-200 border-emerald-300',  label: '5–20%'   },
            { cls: 'bg-yellow-300 border-yellow-400',    label: '20–45%'  },
            { cls: 'bg-orange-400 border-orange-500',    label: '45–70%'  },
            { cls: 'bg-red-700 border-red-800',          label: '>70%'    },
            { cls: 'bg-red-100 ring-1 ring-red-400',     label: 'Outlier' },
            { cls: 'bg-slate-300 border-slate-400',      label: 'DMSO'    },
            { cls: 'bg-violet-200 border-violet-400',    label: 'Ref ctrl'},
          ].map(({ cls, label }) => (
            <span key={label} className="flex items-center gap-1">
              <span className={`w-3 h-3 rounded-sm border inline-block ${cls}`} />
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Column headers with concentration labels */}
      <div className="flex items-end mb-1" style={{ marginLeft: 152 }}>
        {COLS.map((c, i) => {
          const isCtrl = i >= 10
          const ctrlLabel = i === 10 ? 'DMSO' : 'Ref'
          const concUm = [0.001,0.003,0.01,0.03,0.1,0.3,1,3,10,30][i]
          return (
            <div key={c} className="w-7 flex flex-col items-center">
              {isCtrl
                ? <span className="text-[8px] text-slate-400 leading-none mb-0.5 font-semibold">{ctrlLabel}</span>
                : <span className="text-[8px] text-gray-400 leading-none mb-0.5">{concLabel(concUm)}</span>
              }
              <span className="text-[9px] text-gray-300 font-mono">{parseInt(c)}</span>
            </div>
          )
        })}
      </div>

      {ROWS.map(row => {
        const rowWells = wells.filter(w => w.well_position.startsWith(row))
        const isHighlighted = rowWells.some(w => w.scientist_name === highlightScientist)
        const scientist = rowWells[0]?.scientist_name ?? ''
        const compound  = rowWells[0]?.compound_id   ?? ''
        return (
          <div key={row} className={`flex items-center mb-0.5 rounded ${isHighlighted ? 'bg-blue-50' : ''}`}>
            {/* Row label: letter + scientist name + compound */}
            <div className="flex items-center gap-1.5 shrink-0" style={{ width: 148 }}>
              <span className={`text-[10px] font-mono font-bold w-4 shrink-0 ${isHighlighted ? 'text-blue-700' : 'text-gray-400'}`}>
                {row}
              </span>
              {scientist ? (
                <div className="min-w-0">
                  <p className={`text-[10px] font-semibold leading-none truncate ${isHighlighted ? 'text-blue-700' : 'text-gray-700'}`}>
                    {scientist}
                  </p>
                  <p className="text-[9px] text-gray-400 font-mono leading-none mt-0.5 truncate">{compound}</p>
                </div>
              ) : (
                <p className="text-[9px] text-gray-300 italic">pending review</p>
              )}
            </div>

            {/* Wells */}
            {COLS.map(col => {
              const pos = `${row}${col}`
              const well = wellMap.get(pos)
              if (!well) return (
                <div key={col} title={`${pos} — empty / pending HITL`}
                  className="w-7 h-5 mx-px rounded-sm bg-gray-100 border border-gray-200" />
              )
              const tooltip = well.quality === 'ctrl_neg'
                ? `${pos} · ${well.scientist_name} · DMSO control · ${well.rfu_mean?.toLocaleString()} RFU`
                : well.quality === 'ctrl_pos'
                  ? `${pos} · ${well.scientist_name} · Ref inhibitor · ${well.rfu_mean?.toLocaleString()} RFU`
                  : `${pos} · ${well.scientist_name} · ${concLabel(well.conc_um)} · ${well.response.toFixed(1)}% inhibition`
              return (
                <div key={col}
                  title={tooltip}
                  className={`w-7 h-5 mx-px rounded-sm border cursor-default ${responseBg(well.response, well.quality)}`}
                />
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

// ── Dose-Response SVG Curve ───────────────────────────────────────────────────

function DoseResponseCurve({ experiment }: { experiment: CompoundExperiment }) {
  const { ec50_um, hill_slope, signal: emax, curve_data, compound_id, r_squared, neg_ctrl_mean, pos_ctrl_mean } = experiment
  if (!ec50_um || !hill_slope || !emax || curve_data.length === 0) return null

  // Chart geometry
  const W = 520, H = 280
  const ml = 58, mr = 20, mt = 24, mb = 52
  const pw = W - ml - mr   // plot width
  const ph = H - mt - mb   // plot height

  const LOG_MIN = Math.log10(0.0008)
  const LOG_MAX = Math.log10(400)
  const xPx  = (um: number) => ml + (Math.log10(um) - LOG_MIN) / (LOG_MAX - LOG_MIN) * pw
  const yPx  = (pct: number) => mt + ph - (Math.max(0, Math.min(105, pct)) / 105) * ph

  // Smooth fitted sigmoid (200 points)
  const nPts = 200
  const sigmoidPts: string[] = []
  for (let i = 0; i <= nPts; i++) {
    const logC = LOG_MIN + (LOG_MAX - LOG_MIN) * (i / nPts)
    const c    = Math.pow(10, logC)
    const y    = emax / (1 + Math.pow(ec50_um / Math.max(c, 1e-12), hill_slope))
    sigmoidPts.push(`${i === 0 ? 'M' : 'L'}${xPx(c).toFixed(1)},${yPx(y).toFixed(1)}`)
  }

  // Axis ticks
  const xTicks = [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300]
  const yTicks = [0, 25, 50, 75, 100]

  // EC50 marker position
  const ec50x = xPx(ec50_um)
  const ec50y = yPx(emax / 2)

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Dose–Response Curve</h3>
          <p className="text-sm font-bold text-gray-900 mt-0.5">{compound_id}</p>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-600">
          <span>EC50 <strong className="text-blue-700">{ec50_um.toFixed(3)} µM</strong></span>
          <span>Hill <strong className="text-gray-800">{hill_slope.toFixed(2)}</strong></span>
          <span>R² <strong className={r_squared && r_squared >= 0.9 ? 'text-emerald-700' : 'text-red-600'}>
            {r_squared?.toFixed(3)}
          </strong></span>
        </div>
      </div>

      {(neg_ctrl_mean != null || pos_ctrl_mean != null) && (
        <div className="flex items-center gap-3 text-[11px] text-gray-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 mb-3">
          <span className="font-semibold text-slate-400 uppercase tracking-wider text-[9px] shrink-0">Normalization</span>
          {neg_ctrl_mean != null && (
            <span>DMSO baseline <strong className="text-gray-700">{neg_ctrl_mean.toLocaleString()} RFU</strong></span>
          )}
          {pos_ctrl_mean != null && (
            <span>Ref inhibitor <strong className="text-gray-700">{pos_ctrl_mean.toLocaleString()} RFU</strong></span>
          )}
          {neg_ctrl_mean != null && pos_ctrl_mean != null && (
            <span>Window <strong className="text-blue-700">{(neg_ctrl_mean - pos_ctrl_mean).toLocaleString()} RFU</strong></span>
          )}
        </div>
      )}

      <svg width={W} height={H} className="overflow-visible">
        {/* Grid lines */}
        {yTicks.map(y => (
          <line key={y} x1={ml} x2={ml + pw} y1={yPx(y)} y2={yPx(y)}
            stroke="#e5e7eb" strokeWidth="1" strokeDasharray={y > 0 ? "3 3" : "0"} />
        ))}

        {/* EC50 vertical marker */}
        <line x1={ec50x} x2={ec50x} y1={mt} y2={mt + ph}
          stroke="#2563eb" strokeWidth="1.5" strokeDasharray="5 3" opacity="0.7" />
        <text x={ec50x + 4} y={mt + 14} fontSize="10" fill="#2563eb" fontWeight="600">EC50</text>

        {/* Sigmoid fitted curve */}
        <path d={sigmoidPts.join(' ')} fill="none" stroke="#2563eb" strokeWidth="2.5" strokeLinejoin="round" />

        {/* 50% inhibition horizontal guide */}
        <line x1={ml} x2={ec50x} y1={ec50y} y2={ec50y}
          stroke="#2563eb" strokeWidth="1" strokeDasharray="3 3" opacity="0.4" />

        {/* Data points */}
        {curve_data.map((pt, i) => {
          const cx = xPx(pt.conc_um)
          const cy = yPx(pt.response)
          if (pt.quality === 'critical') return (
            <g key={i}>
              <circle cx={cx} cy={cy} r={7} fill="white" stroke="#dc2626" strokeWidth="2" />
              <line x1={cx - 4.5} y1={cy - 4.5} x2={cx + 4.5} y2={cy + 4.5} stroke="#dc2626" strokeWidth="1.8" />
              <line x1={cx + 4.5} y1={cy - 4.5} x2={cx - 4.5} y2={cy + 4.5} stroke="#dc2626" strokeWidth="1.8" />
            </g>
          )
          return <circle key={i} cx={cx} cy={cy} r={5} fill="#0ea5e9" stroke="white" strokeWidth="1.5" />
        })}

        {/* Y-axis */}
        <line x1={ml} x2={ml} y1={mt} y2={mt + ph} stroke="#9ca3af" strokeWidth="1" />
        {yTicks.map(y => (
          <g key={y}>
            <line x1={ml - 4} x2={ml} y1={yPx(y)} y2={yPx(y)} stroke="#9ca3af" strokeWidth="1" />
            <text x={ml - 7} y={yPx(y) + 4} textAnchor="end" fontSize="10" fill="#6b7280">{y}</text>
          </g>
        ))}
        <text x={14} y={mt + ph / 2} textAnchor="middle" fontSize="11" fill="#6b7280"
          transform={`rotate(-90 14 ${mt + ph / 2})`}>% Inhibition</text>

        {/* X-axis */}
        <line x1={ml} x2={ml + pw} y1={mt + ph} y2={mt + ph} stroke="#9ca3af" strokeWidth="1" />
        {xTicks.map(um => {
          const x = xPx(um)
          if (x < ml || x > ml + pw) return null
          return (
            <g key={um}>
              <line x1={x} x2={x} y1={mt + ph} y2={mt + ph + 4} stroke="#9ca3af" strokeWidth="1" />
              <text x={x} y={mt + ph + 15} textAnchor="middle" fontSize="9" fill="#6b7280">
                {concLabel(um)}
              </text>
            </g>
          )
        })}
        <text x={ml + pw / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="#6b7280">
          Concentration (log scale)
        </text>
      </svg>

      {/* Legend */}
      <div className="flex items-center gap-5 mt-1 text-[11px] text-gray-500">
        <span className="flex items-center gap-1.5">
          <svg width="20" height="10"><line x1="0" y1="5" x2="20" y2="5" stroke="#2563eb" strokeWidth="2.5"/></svg>
          Fitted
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="12" height="12"><circle cx="6" cy="6" r="5" fill="#0ea5e9" stroke="white" strokeWidth="1.5"/></svg>
          Valid
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="16" height="16"><circle cx="8" cy="8" r="7" fill="white" stroke="#dc2626" strokeWidth="2"/><line x1="3.5" y1="3.5" x2="12.5" y2="12.5" stroke="#dc2626" strokeWidth="1.8"/><line x1="12.5" y1="3.5" x2="3.5" y2="12.5" stroke="#dc2626" strokeWidth="1.8"/></svg>
          Critical
        </span>
      </div>
    </div>
  )
}

// ── Shared UI ─────────────────────────────────────────────────────────────────

function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  const initials = name.split('_').map((p: string) => p[0]).join('').slice(0, 2).toUpperCase()
  const cls = { sm: 'w-7 h-7 text-[10px]', md: 'w-9 h-9 text-xs', lg: 'w-11 h-11 text-sm' }[size]
  return (
    <div className={`${cls} rounded-full bg-blue-100 border border-blue-200 flex items-center justify-center text-blue-700 font-bold flex-shrink-0`}>
      {initials}
    </div>
  )
}

function QualityBadge({ quality }: { quality: string | null }) {
  if (!quality) return null
  const styles: Record<string, string> = {
    excellent: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    good:      'bg-blue-50 text-blue-700 border-blue-200',
    fair:      'bg-amber-50 text-amber-700 border-amber-200',
    poor:      'bg-red-50 text-red-700 border-red-200',
  }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold ${styles[quality] ?? 'bg-gray-100 text-gray-500'}`}>
      {quality}
    </span>
  )
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

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function GDSAdmin() {
  const router = useRouter()
  const [authed,       setAuthed]       = useState(false)
  const [users,        setUsers]        = useState<GDSUser[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [search,       setSearch]       = useState('')
  const [selected,     setSelected]     = useState<GDSUser | null>(null)
  const [experiments,  setExperiments]  = useState<CompoundExperiment[]>([])
  const [expLoading,   setExpLoading]   = useState(false)
  const [plateWells,   setPlateWells]   = useState<PlateWell[]>([])
  const [plateBc,      setPlateBc]      = useState<string>('')
  const [activeExp,    setActiveExp]    = useState<CompoundExperiment | null>(null)

  const fetchUsers = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await fetch(`${API}/api/gds/users`)
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      const data = await res.json()
      setUsers(data.users)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (localStorage.getItem('gds_auth') === 'true') {
      setAuthed(true); fetchUsers()
    } else {
      router.replace('/login')
    }
  }, [router, fetchUsers])

  const logout = () => { localStorage.removeItem('gds_auth'); router.replace('/login') }

  const selectUser = async (u: GDSUser) => {
    setSelected(u); setExperiments([]); setPlateWells([]); setPlateBc(''); setActiveExp(null)
    setExpLoading(true)
    try {
      const res = await fetch(`${API}/api/gds/users/${u.gds_user_id}/experiments`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      const exps: CompoundExperiment[] = data.experiments
      setExperiments(exps)
      if (exps.length > 0) {
        setActiveExp(exps[0])
        const bc = exps[0].plate_barcode
        if (bc) {
          setPlateBc(bc)
          const pr = await fetch(`${API}/api/gds/plates/${bc}`)
          if (pr.ok) {
            const pd = await pr.json()
            const baseWells: PlateWell[] = pd.wells ?? []

            // Build ctrl wells from neg_ctrl_mean / pos_ctrl_mean per scientist
            const scientists: Array<{ scientist_name: string; neg_ctrl_mean: number | null; pos_ctrl_mean: number | null }> = pd.scientists ?? []
            const sciCtrlMap = new Map(scientists.map(s => [s.scientist_name, s]))
            const sciRowMap = new Map<string, string>()
            for (const w of baseWells) {
              if (!sciRowMap.has(w.scientist_name)) sciRowMap.set(w.scientist_name, w.well_position[0])
            }
            const ctrlWells: PlateWell[] = []
            for (const [sciName, row] of sciRowMap) {
              const info = sciCtrlMap.get(sciName)
              if (!info) continue
              if (info.neg_ctrl_mean != null) ctrlWells.push({
                scientist_name: sciName, compound_id: 'DMSO ctrl', well_position: `${row}11`,
                conc_um: 0, response: 0, quality: 'ctrl_neg', rfu_mean: info.neg_ctrl_mean,
              })
              if (info.pos_ctrl_mean != null) ctrlWells.push({
                scientist_name: sciName, compound_id: 'Pos ctrl', well_position: `${row}12`,
                conc_um: 0, response: 100, quality: 'ctrl_pos', rfu_mean: info.pos_ctrl_mean,
              })
            }
            setPlateWells([...baseWells, ...ctrlWells])
          }
        }
      }
    } catch { setExperiments([]) }
    finally { setExpLoading(false) }
  }

  const filtered = users.filter(u =>
    !search || u.name.toLowerCase().includes(search.toLowerCase()) ||
    u.role.toLowerCase().includes(search.toLowerCase())
  )

  if (!authed) return null

  return (
    <div className="flex h-screen bg-gray-50 overflow-hidden" style={{ fontFamily: 'Inter, system-ui, sans-serif' }}>

      {/* Sidebar */}
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
          <NavItem icon={TrendingUp}      label="Dose–Response" />
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
          <button onClick={logout} className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-xs text-gray-500 hover:bg-gray-100 transition-colors">
            <LogOut size={13} /> Sign out
          </button>
        </div>
      </aside>

      {/* Right panel */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

        {/* Summary strip */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
          <div className="grid grid-cols-4 gap-4">
            {[
              { label: 'Total Scientists', value: users.length,       color: 'text-blue-700',    bg: 'bg-blue-50',    border: 'border-blue-100'    },
              { label: 'Compounds',        value: users.reduce((s, u) => s + u.experiment_count, 0), color: 'text-indigo-700', bg: 'bg-indigo-50', border: 'border-indigo-100' },
              { label: 'Median EC50',      value: (() => { const v = users.filter(u => u.avg_ec50).map(u => u.avg_ec50!).sort((a,b)=>a-b); return v.length ? v[Math.floor(v.length/2)].toFixed(2)+' µM' : '—' })(), color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-100' },
              { label: 'Flagged Curves',   value: users.filter(u => u.avg_r_squared != null && u.avg_r_squared < 0.9).length, color: 'text-amber-700', bg: 'bg-amber-50', border: 'border-amber-100' },
            ].map(({ label, value, color, bg, border }) => (
              <div key={label} className={`rounded-xl border ${border} ${bg} px-4 py-3`}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-500 mb-1">{label}</p>
                <p className={`text-2xl font-bold ${color}`}>{loading ? '—' : value}</p>
              </div>
            ))}
          </div>
        </div>

        {/* List + Detail */}
        <div className="flex flex-1 min-h-0 overflow-hidden">

          {/* Scientist list */}
          <div className="w-80 flex flex-col border-r border-gray-200 bg-white flex-shrink-0">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-bold text-gray-900">Scientists</h2>
                <p className="text-xs text-gray-400 mt-0.5">{filtered.length} of {users.length}</p>
              </div>
              <button onClick={fetchUsers} className="p-1.5 rounded-lg text-gray-400 hover:text-blue-600 hover:bg-blue-50 transition-colors">
                <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              </button>
            </div>
            <div className="px-4 py-3 border-b border-gray-100">
              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input type="text" placeholder="Search name or dept…" value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              {loading && <div className="flex justify-center py-16"><Loader2 size={20} className="animate-spin text-blue-400" /></div>}
              {error && <div className="m-4 bg-red-50 border border-red-200 rounded-lg p-3 flex gap-2"><AlertTriangle size={14} className="text-red-500 mt-0.5" /><p className="text-xs text-red-600">{error}</p></div>}
              {!loading && users.length === 0 && (
                <div className="flex flex-col items-center py-16 px-6 text-center">
                  <Users size={28} className="text-gray-300 mb-3" />
                  <p className="text-sm text-gray-500">No scientists yet</p>
                  <p className="text-xs text-gray-400">Run the AI agent in ABASE → approve in HITL</p>
                </div>
              )}
              {filtered.map(u => {
                const flagged = u.avg_r_squared != null && u.avg_r_squared < 0.9
                return (
                  <button key={u.gds_user_id} onClick={() => selectUser(u)}
                    className={`w-full text-left px-4 py-3 border-b border-gray-50 hover:bg-gray-50 transition-colors flex items-center gap-3 ${selected?.gds_user_id === u.gds_user_id ? 'bg-blue-50' : ''}`}>
                    <Avatar name={u.name} size="sm" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-sm font-semibold text-gray-900 truncate">{u.name}</p>
                        {flagged && <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700 font-bold shrink-0">R²↓</span>}
                      </div>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${DEPT_COLOR[u.role] ?? 'bg-gray-100 text-gray-600'}`}>{u.role}</span>
                        {u.avg_ec50 != null && <span className="text-[10px] text-gray-400">EC50 {u.avg_ec50.toFixed(2)} µM</span>}
                      </div>
                    </div>
                    <ChevronRight size={14} className="text-gray-300 shrink-0" />
                  </button>
                )
              })}
            </div>
          </div>

          {/* Detail panel */}
          <div className="flex-1 overflow-y-auto">
            {selected ? (
              <div className="p-6 space-y-5 max-w-5xl">

                {/* Header */}
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-4">
                    <Avatar name={selected.name} size="lg" />
                    <div>
                      <h1 className="text-xl font-bold text-gray-900">{selected.name}</h1>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <span className={`text-xs px-2 py-0.5 rounded font-semibold ${DEPT_COLOR[selected.role] ?? 'bg-gray-100 text-gray-600'}`}>{selected.role}</span>
                        {selected.avg_ec50 != null && <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full border border-blue-100 font-semibold">EC50 avg: {selected.avg_ec50.toFixed(3)} µM</span>}
                        {selected.avg_r_squared != null && <span className={`text-xs px-2 py-0.5 rounded-full border font-semibold ${selected.avg_r_squared >= 0.9 ? 'bg-emerald-50 text-emerald-700 border-emerald-100' : 'bg-red-50 text-red-700 border-red-100'}`}>R² {selected.avg_r_squared.toFixed(3)}</span>}
                      </div>
                    </div>
                  </div>
                  <button onClick={() => { setSelected(null); setExperiments([]) }} className="text-gray-400 hover:text-gray-600 p-1"><X size={18} /></button>
                </div>

                {expLoading ? (
                  <div className="flex justify-center py-16"><Loader2 size={22} className="animate-spin text-blue-400" /></div>
                ) : experiments.length === 0 ? (
                  <p className="text-center py-16 text-sm text-gray-400">No compound records found.</p>
                ) : (
                  <>
                    {/* Compound selector (if multiple) */}
                    {experiments.length > 1 && (
                      <div className="flex gap-2 flex-wrap">
                        {experiments.map(exp => (
                          <button key={exp.experiment_id} onClick={() => setActiveExp(exp)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${activeExp?.experiment_id === exp.experiment_id ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-200 hover:border-blue-300'}`}>
                            {exp.compound_id}
                          </button>
                        ))}
                      </div>
                    )}

                    {/* EC50 metric cards */}
                    {activeExp && (
                      <div className="grid grid-cols-5 gap-3">
                        {[
                          { label: 'EC50',     value: activeExp.ec50_um != null ? `${activeExp.ec50_um.toFixed(3)} µM` : '—', alert: false },
                          { label: 'Hill n',   value: activeExp.hill_slope?.toFixed(3) ?? '—',    alert: false },
                          { label: 'R²',       value: activeExp.r_squared?.toFixed(4) ?? '—',     alert: (activeExp.r_squared ?? 1) < 0.9 },
                          { label: 'Emax',     value: activeExp.signal != null ? `${activeExp.signal.toFixed(1)}%` : '—', alert: false },
                          { label: 'Points',   value: activeExp.num_concentration_points ?? '—',   alert: false },
                        ].map(({ label, value, alert }) => (
                          <div key={label} className="bg-white border border-gray-200 rounded-xl px-4 py-3 shadow-sm text-center">
                            <p className="text-[10px] uppercase tracking-widest text-gray-400 font-semibold mb-1">{label}</p>
                            <p className={`text-lg font-bold ${alert ? 'text-red-600' : 'text-blue-700'}`}>{value}</p>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Curve quality + assay info */}
                    {activeExp && (
                      <div className="flex items-center gap-3 text-sm text-gray-600">
                        <QualityBadge quality={activeExp.curve_quality} />
                        {activeExp.assay_type && <span className="text-xs bg-gray-100 px-2 py-0.5 rounded text-gray-600">{activeExp.assay_type}</span>}
                        {activeExp.plate_barcode && <span className="text-xs text-gray-400 font-mono">{activeExp.plate_barcode}</span>}
                        <span className="text-xs text-gray-400">
                          Approved {activeExp.approved_by} · {new Date(activeExp.approved_at).toLocaleDateString()}
                        </span>
                      </div>
                    )}

                    {/* 96-well plate heatmap — all scientists on the same plate */}
                    {plateBc && plateWells.length > 0 && (
                      <PlateHeatmap
                        wells={plateWells}
                        highlightScientist={selected.name}
                        plateBarcode={plateBc}
                      />
                    )}

                    {/* Dose-response curve */}
                    {activeExp && <DoseResponseCurve experiment={activeExp} />}
                  </>
                )}
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center text-center px-8 h-full">
                <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mx-auto mb-5">
                  <FlaskRound size={28} className="text-blue-400" />
                </div>
                <p className="text-base font-semibold text-gray-700 mb-2">
                  {users.length === 0 ? 'No scientists migrated yet' : 'Select a scientist'}
                </p>
                <p className="text-sm text-gray-400 max-w-xs">
                  {users.length === 0
                    ? 'Run the AI agent in ABASE → approve in HITL → data appears here'
                    : 'Click a scientist to view their dose–response curve and plate heatmap'}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
