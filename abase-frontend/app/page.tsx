'use client'

import { useState, useEffect, CSSProperties } from 'react'
import { useRouter } from 'next/navigation'
import { Loader2, RefreshCw, ArrowRight, Brain, Wrench, CheckCircle, AlertCircle, Sparkles } from 'lucide-react'

const API = 'http://localhost:8001'

// ── Types ─────────────────────────────────────────────────────────────────────

interface AbaseUser {
  id:                  number
  name:                string
  department:          string
  email:               string | null
  last_login:          string | null
  active_time_minutes: number
}

interface Experiment {
  id:               number
  plate_barcode:    string
  well_position:    string
  raw_value:        number
  recorded_at:      string
  compound_id?:     string | null
  concentration_um?: number | null
  assay_type?:      string | null
}

interface TraceStep {
  type:    'thought' | 'action' | 'result' | 'summary'
  content: string
}

// ── Style constants ────────────────────────────────────────────────────────────

const S = {
  pageBg:        '#d4d0c8',
  panelBg:       '#f0f0f0',
  titleBg:       '#000080',
  titleText:     '#ffffff',
  borderLight:   '#dfdfdf',
  borderDark:    '#808080',
  borderDarker:  '#404040',
  selectedBg:    '#000080',
  selectedText:  '#ffffff',
  font:          'Tahoma, Arial, sans-serif',
  mono:          "'Courier New', Courier, monospace",
  fontSize:      '12px',
  danger:        '#8b0000',
}

const raised: CSSProperties = {
  borderTop:    `2px solid ${S.borderLight}`,
  borderLeft:   `2px solid ${S.borderLight}`,
  borderRight:  `2px solid ${S.borderDark}`,
  borderBottom: `2px solid ${S.borderDark}`,
}

const sunken: CSSProperties = {
  borderTop:    `2px solid ${S.borderDark}`,
  borderLeft:   `2px solid ${S.borderDark}`,
  borderRight:  `2px solid ${S.borderLight}`,
  borderBottom: `2px solid ${S.borderLight}`,
}

const classicBtn: CSSProperties = {
  background:   S.pageBg,
  fontFamily:   S.font,
  fontSize:     S.fontSize,
  cursor:       'pointer',
  padding:      '3px 12px',
  borderTop:    `1px solid ${S.borderLight}`,
  borderLeft:   `1px solid ${S.borderLight}`,
  borderRight:  `1px solid ${S.borderDarker}`,
  borderBottom: `1px solid ${S.borderDarker}`,
  color:        '#000000',
  minWidth:     80,
}

const dangerBtn: CSSProperties = {
  ...classicBtn,
  background:  S.danger,
  color:       '#ffffff',
  fontWeight:  'bold',
  padding:     '5px 20px',
  fontSize:    '12px',
  letterSpacing: '0.5px',
}

// ── Trace rendering ────────────────────────────────────────────────────────────

function toTraceSteps(rawTrace: Record<string, string>[]): TraceStep[] {
  return rawTrace.map(step => {
    const action = step.action ?? ''
    if (action === 'AGENT_START')
      return { type: 'thought', content: step.message ?? 'Agent started.' }
    if (action === 'AGENT_DONE' || action === 'MAX_TURNS_REACHED')
      return { type: 'summary', content: step.message ?? action }
    if (action.startsWith('TOOL_CALL:')) {
      const tool = action.replace('TOOL_CALL:', '')
      const args = step.args ? ` args=${step.args.slice(0, 60)}` : ''
      return { type: 'action', content: `Calling ${tool}(${args})` }
    }
    if (action.startsWith('TOOL_RESULT:')) {
      const tool   = action.replace('TOOL_RESULT:', '')
      const status = step.status ?? ''
      const msg    = step.summary ?? step.message ?? step.staged_rows ?? ''
      const isErr  = status === 'error'
      return { type: 'result', content: `${tool} → ${isErr ? 'ERROR: ' : ''}${msg}`.slice(0, 120) }
    }
    return { type: 'thought', content: `${action}: ${step.message ?? ''}` }
  })
}

function TraceEntry({ step }: { step: TraceStep }) {
  const isError = step.type === 'result' && step.content.toLowerCase().includes('error')
  const cfg = {
    thought: { icon: <Brain size={11} style={{ color: '#888', flexShrink: 0, marginTop: 1 }} />, color: '#888' },
    action:  { icon: <Wrench size={11} style={{ color: '#00aaff', flexShrink: 0, marginTop: 1 }} />, color: '#00aaff' },
    result:  { icon: isError
      ? <AlertCircle size={11} style={{ color: '#ff4444', flexShrink: 0, marginTop: 1 }} />
      : <CheckCircle size={11} style={{ color: '#00cc66', flexShrink: 0, marginTop: 1 }} />, color: isError ? '#ff4444' : '#00cc66' },
    summary: { icon: <Sparkles size={11} style={{ color: '#ffcc00', flexShrink: 0, marginTop: 1 }} />, color: '#ffcc00' },
  }[step.type]

  return (
    <div style={{ display: 'flex', gap: 6, padding: '2px 0', borderBottom: '1px solid #1a1a1a', alignItems: 'flex-start' }}>
      {cfg.icon}
      <span style={{ fontFamily: S.mono, fontSize: 10, color: cfg.color, wordBreak: 'break-all' }}>
        [{step.type.toUpperCase()}] {step.content}
      </span>
    </div>
  )
}

// ── Confirmation Modal ────────────────────────────────────────────────────────

// ── Main Page ─────────────────────────────────────────────────────────────────

type View = 'scientists' | 'admin'

export default function AbaseAdmin() {
  const router = useRouter()
  const [authed,       setAuthed]       = useState(false)
  const [view,         setView]         = useState<View>('scientists')
  const [users,        setUsers]        = useState<AbaseUser[]>([])
  const [usersLoading, setUsersLoading] = useState(true)
  const [search,       setSearch]       = useState('')
  const [selected,     setSelected]     = useState<AbaseUser | null>(null)
  const [experiments,  setExperiments]  = useState<Experiment[]>([])
  const [expLoading,   setExpLoading]   = useState(false)
  const [agentPhase,   setAgentPhase]   = useState<'idle' | 'loading' | 'playing' | 'done' | 'error'>('idle')
  const [allSteps,     setAllSteps]     = useState<TraceStep[]>([])
  const [visible,      setVisible]      = useState<TraceStep[]>([])
  const [traceId,      setTraceId]      = useState<string | null>(null)
  const [agentResult,  setAgentResult]  = useState<{ staged_row_count?: number; auto_approved?: number; pending_review?: number } | null>(null)
  const [errorMsg,     setErrorMsg]     = useState('')
  const [statusMsg,    setStatusMsg]    = useState('Ready')

  useEffect(() => {
    if (localStorage.getItem('abase_auth') === 'true') setAuthed(true)
    else router.replace('/login')
  }, [router])

  const fetchUsers = async () => {
    setUsersLoading(true)
    setStatusMsg('Loading scientist registry...')
    try {
      const res = await fetch(`${API}/api/abase/users`)
      if (!res.ok) throw new Error(`Server ${res.status}`)
      const data = await res.json()
      setUsers(data.users)
      setStatusMsg(`${data.users.length} scientists loaded`)
    } catch (e) {
      setStatusMsg('ERROR: Failed to load users')
      console.error(e)
    } finally {
      setUsersLoading(false)
    }
  }

  useEffect(() => { fetchUsers() }, [])

  const selectUser = async (user: AbaseUser) => {
    setSelected(user)
    setExpLoading(true)
    setStatusMsg(`Loading experiments for ${user.name}...`)
    try {
      const res = await fetch(`${API}/api/abase/users/${user.id}`)
      if (!res.ok) throw new Error(`Server ${res.status}`)
      const data = await res.json()
      setExperiments(data.experiments)
      setStatusMsg(`${data.experiments.length} experiment records loaded for ${user.name}`)
    } catch {
      setExperiments([])
      setStatusMsg('ERROR: Failed to load experiments')
    } finally {
      setExpLoading(false)
    }
  }

  const playback = (steps: TraceStep[], tid: string, result: typeof agentResult) => {
    setAllSteps(steps); setVisible([]); setAgentPhase('playing')
    let i = 0
    const tick = () => {
      i++
      setVisible(steps.slice(0, i))
      setStatusMsg(`Agent running — step ${i} of ${steps.length}`)
      if (i < steps.length) setTimeout(tick, 500)
      else { setTraceId(tid); setAgentResult(result); setAgentPhase('done'); setStatusMsg(`Migration complete — trace ID: ${tid}`) }
    }
    setTimeout(tick, 500)
  }


  const filtered = users.filter(u =>
    !search ||
    u.name.toLowerCase().includes(search.toLowerCase()) ||
    u.department.toLowerCase().includes(search.toLowerCase())
  )

  const isRunning = agentPhase === 'loading' || agentPhase === 'playing'
  const totalWells = users.length * 4

  if (!authed) return null

  const NAV_ITEMS: { id: View | null; label: string; restricted?: boolean }[] = [
    { id: null,         label: 'Dashboard' },
    { id: 'scientists', label: 'Scientists' },
    { id: null,         label: 'Experiments' },
    { id: null,         label: 'System Log' },
    { id: null,         label: 'Settings' },
    { id: 'admin',      label: 'Administration', restricted: true },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: S.font, fontSize: S.fontSize, background: S.pageBg, overflow: 'hidden' }}>

      {/* ── Top menu bar ── */}
      <div style={{ background: S.pageBg, borderBottom: `1px solid ${S.borderDark}`, padding: '2px 8px', display: 'flex', alignItems: 'center', gap: 16, fontSize: 11 }}>
        <span style={{ fontWeight: 'bold' }}>File</span>
        <span>Edit</span>
        <span>View</span>
        <span>Tools</span>
        <span>Help</span>
      </div>

      {/* ── Title bar ── */}
      <div style={{ background: S.titleBg, color: S.titleText, padding: '4px 10px', fontSize: 12, fontWeight: 'bold', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>■ ABASE — Advanced Biological Analysis System Enterprise &nbsp;[PRODUCTION]</span>
        <span style={{ fontSize: 11, fontWeight: 'normal', opacity: 0.8 }}>v2.4.1 · us-west-2</span>
      </div>

      {/* ── Body ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── Sidebar ── */}
        <div style={{ ...raised, width: 160, background: S.panelBg, display: 'flex', flexDirection: 'column', flexShrink: 0 }}>
          <div style={{ background: S.titleBg, color: S.titleText, padding: '4px 8px', fontSize: 11, fontWeight: 'bold' }}>
            Navigation
          </div>
          <div style={{ flex: 1, padding: '4px 0' }}>
            {NAV_ITEMS.map((item, i) => {
              const isActive = item.id === view
              const isSep = i === 4
              return (
                <div key={item.label}>
                  {isSep && <div style={{ borderTop: `1px solid ${S.borderDark}`, margin: '4px 0' }} />}
                  <div
                    onClick={() => item.id && setView(item.id)}
                    style={{
                      padding: '4px 10px',
                      cursor: item.id ? 'pointer' : 'default',
                      background: isActive ? S.selectedBg : 'transparent',
                      color: isActive ? S.selectedText : item.restricted ? S.danger : '#000',
                      fontSize: 11,
                      fontWeight: item.restricted ? 'bold' : 'normal',
                      userSelect: 'none',
                    }}
                  >
                    {item.restricted ? '⚠ ' : ''}{item.label}
                  </div>
                </div>
              )
            })}
          </div>

          <div style={{ borderTop: `1px solid ${S.borderDark}`, padding: '8px', fontSize: 10, color: '#555' }}>
            <div>ABASE v2.4 · PROD</div>
            <div style={{ marginTop: 2, color: '#888' }}>us-west-2 · Connected</div>
            <button
              onClick={() => { localStorage.removeItem('abase_auth'); router.replace('/login') }}
              style={{ ...classicBtn, marginTop: 8, width: '100%', fontSize: 10 }}
            >
              Sign Out
            </button>
          </div>
        </div>

        {/* ── Content ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* ── SCIENTISTS VIEW ── */}
          {view === 'scientists' && (
            <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

              {/* User list */}
              <div style={{ ...raised, width: 300, background: S.panelBg, display: 'flex', flexDirection: 'column', flexShrink: 0, margin: 6 }}>
                <div style={{ background: S.titleBg, color: '#fff', padding: '3px 8px', fontSize: 11, fontWeight: 'bold', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>Scientists Registry ({users.length})</span>
                  <button
                    onClick={fetchUsers}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: '#fff', padding: 0, display: 'flex', alignItems: 'center' }}
                  >
                    <RefreshCw size={11} className={usersLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {/* Search */}
                <div style={{ padding: '4px 6px', borderBottom: `1px solid ${S.borderDark}` }}>
                  <input
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Search by name or department..."
                    style={{ ...sunken, fontFamily: S.font, fontSize: 11, width: '100%', padding: '2px 6px', boxSizing: 'border-box', background: '#fff', outline: 'none' }}
                  />
                </div>

                {/* List */}
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  {usersLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 80, gap: 6, color: '#555', fontSize: 11 }}>
                      <Loader2 size={14} className="animate-spin" /> Loading...
                    </div>
                  ) : filtered.map((u, i) => {
                    const isSelected = selected?.id === u.id
                    return (
                      <div
                        key={u.id}
                        onClick={() => selectUser(u)}
                        style={{
                          padding: '4px 8px',
                          background: isSelected ? S.selectedBg : i % 2 === 0 ? '#fff' : '#f5f5f5',
                          color: isSelected ? '#fff' : '#000',
                          borderBottom: `1px solid ${S.borderLight}`,
                          cursor: 'pointer',
                          userSelect: 'none',
                        }}
                      >
                        <div style={{ fontWeight: 'bold', fontSize: 11 }}>{u.name}</div>
                        <div style={{ fontSize: 10, color: isSelected ? '#ccd' : '#666', marginTop: 1 }}>
                          {u.department} &nbsp;·&nbsp; login {u.last_login ? new Date(u.last_login).toLocaleDateString() : 'N/A'}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Detail panel */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: 6, paddingLeft: 0 }}>
                <div style={{ ...raised, flex: 1, background: S.panelBg, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  <div style={{ background: S.titleBg, color: '#fff', padding: '3px 8px', fontSize: 11, fontWeight: 'bold' }}>
                    {selected ? `Experiment Records — ${selected.name}` : 'Experiment Records'}
                  </div>

                  {!selected ? (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#888', fontSize: 11 }}>
                      Select a scientist from the registry to view experiment records.
                    </div>
                  ) : expLoading ? (
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, gap: 6, color: '#555', fontSize: 11 }}>
                      <Loader2 size={14} className="animate-spin" /> Loading records...
                    </div>
                  ) : (
                    <>
                      {/* Scientist info bar */}
                      <div style={{ padding: '6px 10px', borderBottom: `1px solid ${S.borderDark}`, background: '#e8e8e8', fontSize: 11, display: 'flex', gap: 24 }}>
                        <span><strong>ID:</strong> {selected.id}</span>
                        <span><strong>Dept:</strong> {selected.department}</span>
                        {selected.email && <span><strong>Email:</strong> {selected.email}</span>}
                        <span><strong>Active:</strong> {Math.round(selected.active_time_minutes / 60)}h</span>
                        <span><strong>Wells:</strong> {experiments.length}</span>
                      </div>

                      {/* Table */}
                      <div style={{ flex: 1, overflowY: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                          <thead>
                            <tr style={{ background: '#d0d0d0', position: 'sticky', top: 0 }}>
                              {['Well Position', 'Raw Value', 'Compound ID', 'Conc (µM)', 'Assay Type', 'Plate Barcode', 'Recorded At', 'Flag'].map(h => (
                                <th key={h} style={{ padding: '4px 10px', textAlign: 'left', borderBottom: `2px solid ${S.borderDark}`, borderRight: `1px solid ${S.borderDark}`, fontSize: 10, fontWeight: 'bold' }}>
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {experiments.map((exp, i) => {
                              const isLow  = exp.raw_value < 1.5
                              const isHigh = exp.raw_value > 18
                              const flag   = isLow ? '⚠ LOW' : isHigh ? '⚠ HIGH' : ''
                              return (
                                <tr key={exp.id} style={{ background: i % 2 === 0 ? '#fff' : '#f5f5f5', borderBottom: `1px solid ${S.borderLight}` }}>
                                  <td style={{ padding: '3px 10px', fontFamily: S.mono, fontWeight: 'bold' }}>{exp.well_position}</td>
                                  <td style={{ padding: '3px 10px', fontFamily: S.mono, color: flag ? S.danger : '#000', fontWeight: flag ? 'bold' : 'normal' }}>
                                    {exp.raw_value.toFixed(4)}
                                  </td>
                                  <td style={{ padding: '3px 10px', fontFamily: S.mono, color: '#555' }}>{exp.compound_id ?? '—'}</td>
                                  <td style={{ padding: '3px 10px', fontFamily: S.mono, color: '#555' }}>{exp.concentration_um != null ? exp.concentration_um.toFixed(1) : '—'}</td>
                                  <td style={{ padding: '3px 10px', color: '#555' }}>{exp.assay_type ?? '—'}</td>
                                  <td style={{ padding: '3px 10px', fontFamily: S.mono, color: '#555' }}>{exp.plate_barcode}</td>
                                  <td style={{ padding: '3px 10px', color: '#555' }}>{new Date(exp.recorded_at).toLocaleDateString()}</td>
                                  <td style={{ padding: '3px 10px', color: S.danger, fontWeight: 'bold', fontSize: 10 }}>{flag}</td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── ADMINISTRATION VIEW ── */}
          {view === 'admin' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: 10 }}>

              {/* Header */}
              <div style={{ ...raised, background: S.panelBg, marginBottom: 10 }}>
                <div style={{ background: S.danger, color: '#fff', padding: '4px 10px', fontSize: 11, fontWeight: 'bold' }}>
                  ⚠ System Administration — Restricted Access
                </div>
                <div style={{ padding: '6px 10px', fontSize: 11, color: '#333', borderBottom: `1px solid ${S.borderDark}` }}>
                  Actions in this section are irreversible and logged. Unauthorized use is prohibited.
                </div>
              </div>

              {/* Migration operation box */}
              <div style={{ ...raised, background: S.panelBg, marginBottom: 10 }}>
                <div style={{ background: '#333', color: '#fff', padding: '4px 10px', fontSize: 11, fontWeight: 'bold' }}>
                  DATABASE MIGRATION OPERATIONS
                </div>
                <div style={{ padding: 12 }}>

                  {/* System info table */}
                  <table style={{ fontSize: 11, marginBottom: 14, borderCollapse: 'collapse', width: '100%' }}>
                    <tbody>
                      {[
                        ['Source System',    'ABASE (us-west-2)'],
                        ['Target System',    'GDS   (us-east-2)'],
                        ['Scientist Records', usersLoading ? 'Loading...' : `${users.length} records`],
                        ['Well Experiments',  usersLoading ? 'Loading...' : `${totalWells} records (estimated)`],
                        ['Last Migration',     traceId ? traceId : 'No previous migration recorded'],
                      ].map(([k, v]) => (
                        <tr key={k} style={{ borderBottom: `1px solid ${S.borderLight}` }}>
                          <td style={{ padding: '4px 16px 4px 0', color: '#555', width: 180, fontSize: 11 }}>{k}</td>
                          <td style={{ padding: '4px 0', fontFamily: S.mono, fontSize: 11, color: '#000' }}>{v}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  <div style={{ borderTop: `2px solid ${S.borderDark}`, paddingTop: 10, fontSize: 10, color: '#666', fontStyle: 'italic' }}>
                    💡 Migration is now managed from the HITL Console (http://localhost:3000)
                  </div>
                </div>
              </div>

              {/* Agent console — shown once triggered */}
              {(agentPhase !== 'idle') && (
                <div style={{ ...raised, background: S.panelBg, marginBottom: 10 }}>
                  <div style={{ background: '#222', color: '#0f0', padding: '4px 10px', fontSize: 11, fontWeight: 'bold', fontFamily: S.mono, display: 'flex', justifyContent: 'space-between' }}>
                    <span>AGENT CONSOLE — agent@abase ~ POST /api/agent/run</span>
                    <span style={{ color: '#888' }}>{visible.length}/{allSteps.length} steps</span>
                  </div>
                  <div style={{ background: '#111', padding: 8, height: 220, overflowY: 'auto' }}>
                    {agentPhase === 'loading' && (
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: '#0f0', fontFamily: S.mono, fontSize: 11 }}>
                        <Loader2 size={12} className="animate-spin" /> Initializing agent...
                      </div>
                    )}
                    {visible.map((step, i) => <TraceEntry key={i} step={step} />)}
                    {agentPhase === 'playing' && (
                      <span style={{ color: '#0f0', fontFamily: S.mono, fontSize: 11 }}>█</span>
                    )}
                  </div>
                </div>
              )}

              {/* Error */}
              {agentPhase === 'error' && (
                <div style={{ ...sunken, background: '#fff0f0', padding: '8px 12px', marginBottom: 10, borderLeft: `4px solid ${S.danger}`, fontSize: 11 }}>
                  <strong style={{ color: S.danger }}>MIGRATION FAILED:</strong> {errorMsg}
                </div>
              )}

              {/* Success */}
              {agentPhase === 'done' && agentResult && traceId && (
                <div style={{ ...raised, background: '#f0fff0', padding: 12, marginBottom: 10 }}>
                  <div style={{ fontWeight: 'bold', fontSize: 12, marginBottom: 6, color: '#006600' }}>
                    ✓ Migration Completed Successfully
                  </div>
                  <table style={{ fontSize: 11, borderCollapse: 'collapse', marginBottom: 10 }}>
                    <tbody>
                      {[
                        ['Trace ID',         traceId],
                        ['Total Staged',      `${agentResult.staged_row_count} rows`],
                        ['Auto-Approved',     `${agentResult.auto_approved ?? '—'} rows`],
                        ['Pending Review',    `${agentResult.pending_review ?? '—'} rows`],
                      ].map(([k, v]) => (
                        <tr key={k}>
                          <td style={{ padding: '2px 16px 2px 0', color: '#555' }}>{k}</td>
                          <td style={{ fontFamily: S.mono, fontSize: 11 }}>{v}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <a
                    href={`http://localhost:3000/review?trace_id=${traceId}`}
                    target="_blank" rel="noreferrer"
                    style={{ ...dangerBtn, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6, background: '#000080' }}
                  >
                    Proceed to HITL Review Console
                    <ArrowRight size={13} />
                  </a>
                </div>
              )}

            </div>
          )}

        </div>
      </div>

      {/* ── Status bar ── */}
      <div style={{ borderTop: `2px solid ${S.borderDark}`, background: S.pageBg, padding: '2px 8px', fontSize: 10, color: '#333', display: 'flex', justifyContent: 'space-between' }}>
        <span>{statusMsg}</span>
        <span style={{ color: '#666' }}>ABASE v2.4 · PROD · {users.length} scientists · {totalWells} wells · Connected</span>
      </div>

    </div>
  )
}
