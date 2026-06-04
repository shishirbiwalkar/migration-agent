'use client'

import { useState, useEffect, useRef } from 'react'
import {
  Database, Play, RefreshCw, ChevronDown, ChevronRight,
  AlertCircle, CheckCircle2, Clock, Zap, Shield, BarChart3,
  FileText, LogOut, Search, Menu, X, Loader2, Check, XCircle,
} from 'lucide-react'

const API = 'http://localhost:8001'

type NavItem = 'dashboard' | 'migrations' | 'reviews' | 'reports' | 'audit'
type MigrationStatus = 'running' | 'review_pending' | 'approved' | 'completed'

interface MigrationRun {
  trace_id: string
  source: string
  target: string
  status: MigrationStatus
  started_at: string
  total_rows: number
  auto_approved: number
  flagged: number
  pending: number
  scientists?: Scientist[]
}

interface Scientist {
  name: string
  pending_wells: number
}

interface PendingRow {
  trace_id: string
  scientist_name: string
  well_position: string
  signal: number
  risk_level: string
  status: string
}

export default function HITLConsole() {
  const [nav, setNav] = useState<NavItem>('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [migrations, setMigrations] = useState<MigrationRun[]>([])
  const [pendingRows, setPendingRows] = useState<PendingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [showRunModal, setShowRunModal] = useState(false)
  const [search, setSearch] = useState('')

  // Fetch migrations on mount
  useEffect(() => {
    fetchMigrations()
    const interval = setInterval(fetchMigrations, 5000)
    return () => clearInterval(interval)
  }, [])

  const fetchMigrations = async () => {
    try {
      const [pendingRes, completedRes] = await Promise.all([
        fetch(`${API}/api/migrate/pending`),
        fetch(`${API}/api/report/completed`),
      ])
      const pending = await pendingRes.json()
      setMigrations(pending.runs || [])
    } catch (e) {
      console.error('Error fetching migrations:', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* ── SIDEBAR ── */}
      <div className={`${sidebarOpen ? 'w-64' : 'w-20'} bg-slate-900 text-white transition-all duration-300 flex flex-col border-r border-slate-800`}>

        {/* Header */}
        <div className="p-4 border-b border-slate-800 flex items-center justify-between">
          {sidebarOpen && <h1 className="text-lg font-bold">HITL</h1>}
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-1 hover:bg-slate-800 rounded">
            {sidebarOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>

        {/* Search */}
        {sidebarOpen && (
          <div className="p-3 border-b border-slate-800">
            <div className="relative">
              <Search size={16} className="absolute left-2 top-2 text-gray-400" />
              <input
                placeholder="Search migrations..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-8 pr-3 py-2 bg-slate-800 text-sm rounded text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 p-3 space-y-2">
          <NavButton
            icon={<BarChart3 size={20} />}
            label="Dashboard"
            active={nav === 'dashboard'}
            onClick={() => setNav('dashboard')}
            sidebarOpen={sidebarOpen}
          />
          <NavButton
            icon={<Zap size={20} />}
            label="Active Migrations"
            active={nav === 'migrations'}
            onClick={() => setNav('migrations')}
            sidebarOpen={sidebarOpen}
            badge={migrations.filter(m => m.status === 'running' || m.status === 'review_pending').length}
          />
          <NavButton
            icon={<AlertCircle size={20} />}
            label="Pending Reviews"
            active={nav === 'reviews'}
            onClick={() => setNav('reviews')}
            sidebarOpen={sidebarOpen}
            badge={migrations.reduce((sum, m) => sum + m.flagged, 0)}
          />
          <NavButton
            icon={<FileText size={20} />}
            label="Reports"
            active={nav === 'reports'}
            onClick={() => setNav('reports')}
            sidebarOpen={sidebarOpen}
          />
          <NavButton
            icon={<LogOut size={20} />}
            label="Audit Log"
            active={nav === 'audit'}
            onClick={() => setNav('audit')}
            sidebarOpen={sidebarOpen}
          />
        </nav>

        {/* Footer */}
        <div className="p-3 border-t border-slate-800 text-xs text-gray-400">
          {sidebarOpen && (
            <>
              <div className="font-semibold text-white mb-2">HITL Console</div>
              <div>Migration Management System</div>
            </>
          )}
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Top bar */}
        <div className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6">
          <h2 className="text-2xl font-bold text-gray-900">
            {nav === 'dashboard' && 'Migration Dashboard'}
            {nav === 'migrations' && 'Active Migrations'}
            {nav === 'reviews' && 'Pending Reviews'}
            {nav === 'reports' && 'Reports'}
            {nav === 'audit' && 'Audit Log'}
          </h2>
          <div className="flex items-center gap-4">
            {nav === 'dashboard' && (
              <button
                onClick={() => setShowRunModal(true)}
                className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition"
              >
                <Play size={18} />
                Run Migration
              </button>
            )}
            <button onClick={fetchMigrations} className="p-2 hover:bg-gray-100 rounded-lg transition">
              <RefreshCw size={20} className="text-gray-600" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto">
          {nav === 'dashboard' && <DashboardView migrations={migrations} loading={loading} />}
          {nav === 'migrations' && <MigrationsView migrations={migrations} />}
          {nav === 'reviews' && <ReviewsView migrations={migrations} />}
          {nav === 'reports' && <ReportsView />}
          {nav === 'audit' && <AuditView />}
        </div>
      </div>

      {/* ── RUN MIGRATION MODAL ── */}
      {showRunModal && <RunMigrationModal onClose={() => setShowRunModal(false)} onSuccess={fetchMigrations} />}
    </div>
  )
}

// ── Navigation Button ──
function NavButton({
  icon,
  label,
  active,
  onClick,
  sidebarOpen,
  badge,
}: {
  icon: React.ReactNode
  label: string
  active: boolean
  onClick: () => void
  sidebarOpen: boolean
  badge?: number
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-3 rounded-lg font-medium transition ${
        active
          ? 'bg-blue-600 text-white'
          : 'text-gray-300 hover:bg-slate-800'
      }`}
    >
      {icon}
      {sidebarOpen && (
        <>
          <span className="flex-1 text-left">{label}</span>
          {badge !== undefined && badge > 0 && (
            <span className="bg-red-600 text-white text-xs rounded-full w-6 h-6 flex items-center justify-center">
              {badge}
            </span>
          )}
        </>
      )}
    </button>
  )
}

// ── DASHBOARD VIEW ──
function DashboardView({ migrations, loading }: { migrations: MigrationRun[]; loading: boolean }) {
  const activeMigrations = migrations.filter(m => m.status === 'running' || m.status === 'review_pending')
  const completedMigrations = migrations.filter(m => m.status === 'completed')
  const totalFlagged = migrations.reduce((sum, m) => sum + m.flagged, 0)
  const totalApproved = migrations.reduce((sum, m) => sum + m.auto_approved, 0)

  return (
    <div className="p-6 space-y-6">
      {/* Quick Stats */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard icon={<Zap size={24} />} label="Active" value={activeMigrations.length} color="blue" />
        <StatCard icon={<AlertCircle size={24} />} label="Pending Review" value={totalFlagged} color="yellow" />
        <StatCard icon={<Check size={24} />} label="Auto-Approved" value={totalApproved} color="green" />
        <StatCard icon={<CheckCircle2 size={24} />} label="Completed" value={completedMigrations.length} color="emerald" />
      </div>

      {/* Active Migrations */}
      {activeMigrations.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-bold text-gray-900 mb-4">Active Migrations</h3>
          <div className="space-y-4">
            {activeMigrations.map(m => (
              <MigrationCard key={m.trace_id} migration={m} />
            ))}
          </div>
        </div>
      )}

      {/* Recent Completed */}
      {completedMigrations.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-bold text-gray-900 mb-4">Recently Completed</h3>
          <div className="space-y-3">
            {completedMigrations.slice(0, 5).map(m => (
              <div key={m.trace_id} className="flex items-center justify-between p-3 bg-gray-50 rounded">
                <div>
                  <p className="font-medium text-gray-900">{m.trace_id.slice(0, 8)}...</p>
                  <p className="text-sm text-gray-500">{m.started_at}</p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-medium text-gray-900">{m.total_rows} rows</p>
                  <p className="text-xs text-green-600">✓ Approved</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeMigrations.length === 0 && completedMigrations.length === 0 && !loading && (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <Database size={48} className="mx-auto text-gray-300 mb-4" />
          <p className="text-gray-600 text-lg">No migrations yet</p>
          <p className="text-gray-500 text-sm">Click "Run Migration" to start</p>
        </div>
      )}
    </div>
  )
}

// ── STAT CARD ──
function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  const colors = {
    blue: 'bg-blue-50 text-blue-700 border-blue-200',
    yellow: 'bg-yellow-50 text-yellow-700 border-yellow-200',
    green: 'bg-green-50 text-green-700 border-green-200',
    emerald: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  }
  return (
    <div className={`${colors[color as keyof typeof colors]} border rounded-lg p-4`}>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium opacity-75">{label}</p>
          <p className="text-3xl font-bold mt-1">{value}</p>
        </div>
        <div className="opacity-20">{icon}</div>
      </div>
    </div>
  )
}

// ── MIGRATION CARD ──
function MigrationCard({ migration }: { migration: MigrationRun }) {
  const statusColor =
    migration.status === 'running' ? 'bg-blue-50 border-blue-200' :
    migration.status === 'review_pending' ? 'bg-yellow-50 border-yellow-200' :
    'bg-green-50 border-green-200'

  const statusIcon =
    migration.status === 'running' ? <Loader2 size={18} className="animate-spin text-blue-600" /> :
    migration.status === 'review_pending' ? <AlertCircle size={18} className="text-yellow-600" /> :
    <CheckCircle2 size={18} className="text-green-600" />

  return (
    <div className={`${statusColor} border rounded-lg p-4`}>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          {statusIcon}
          <div>
            <p className="font-mono text-sm font-medium text-gray-900">{migration.trace_id.slice(0, 8)}...</p>
            <p className="text-xs text-gray-600">{migration.source} → {migration.target}</p>
          </div>
        </div>
        <span className="px-2 py-1 bg-white rounded text-xs font-medium text-gray-700">
          {migration.status === 'running' ? '🔄 Running' : migration.status === 'review_pending' ? '⏳ Pending Review' : '✓ Completed'}
        </span>
      </div>

      <div className="grid grid-cols-4 gap-2 mb-3">
        <div className="bg-white rounded p-2 text-center">
          <p className="text-xs text-gray-600">Total</p>
          <p className="font-bold text-gray-900">{migration.total_rows}</p>
        </div>
        <div className="bg-white rounded p-2 text-center">
          <p className="text-xs text-gray-600">Auto ✓</p>
          <p className="font-bold text-green-600">{migration.auto_approved}</p>
        </div>
        <div className="bg-white rounded p-2 text-center">
          <p className="text-xs text-gray-600">Flagged</p>
          <p className="font-bold text-yellow-600">{migration.flagged}</p>
        </div>
        <div className="bg-white rounded p-2 text-center">
          <p className="text-xs text-gray-600">Pending</p>
          <p className="font-bold text-blue-600">{migration.pending}</p>
        </div>
      </div>

      {migration.status === 'review_pending' && migration.flagged > 0 && (
        <button className="w-full px-3 py-2 bg-yellow-600 hover:bg-yellow-700 text-white rounded font-medium text-sm transition">
          Review {migration.flagged} Flagged Rows
        </button>
      )}
    </div>
  )
}

// ── MIGRATIONS VIEW ──
function MigrationsView({ migrations }: { migrations: MigrationRun[] }) {
  const active = migrations.filter(m => m.status === 'running' || m.status === 'review_pending')
  return (
    <div className="p-6">
      {active.length > 0 ? (
        <div className="space-y-4">
          {active.map(m => (
            <MigrationCard key={m.trace_id} migration={m} />
          ))}
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <Clock size={48} className="mx-auto text-gray-300 mb-4" />
          <p className="text-gray-600">No active migrations</p>
        </div>
      )}
    </div>
  )
}

// ── REVIEWS VIEW ──
function ReviewsView({ migrations }: { migrations: MigrationRun[] }) {
  const flaggedCount = migrations.reduce((sum, m) => sum + m.flagged, 0)
  return (
    <div className="p-6">
      {flaggedCount > 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-bold text-gray-900 mb-4">{flaggedCount} Rows Pending Review</h3>
          <p className="text-gray-600">Review queue implementation coming soon</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <CheckCircle2 size={48} className="mx-auto text-green-300 mb-4" />
          <p className="text-gray-600 text-lg">No pending reviews</p>
          <p className="text-gray-500">All rows have been approved</p>
        </div>
      )}
    </div>
  )
}

// ── REPORTS VIEW ──
function ReportsView() {
  return (
    <div className="p-6">
      <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
        <FileText size={48} className="mx-auto text-gray-300 mb-4" />
        <p className="text-gray-600">Reports view coming soon</p>
      </div>
    </div>
  )
}

// ── AUDIT LOG VIEW ──
function AuditView() {
  return (
    <div className="p-6">
      <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
        <LogOut size={48} className="mx-auto text-gray-300 mb-4" />
        <p className="text-gray-600">Audit log view coming soon</p>
      </div>
    </div>
  )
}

// ── RUN MIGRATION MODAL ──
function RunMigrationModal({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const [source, setSource] = useState('ABASE')
  const [target, setTarget] = useState('GDS')
  const [running, setRunning] = useState(false)

  const handleRun = async () => {
    setRunning(true)
    try {
      const res = await fetch(`${API}/api/agent/run/async`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_db_url: source === 'ABASE' ? undefined : source,
          target_db_url: target === 'GDS' ? undefined : target,
          initiated_by: 'HITL Console User',
        }),
      })
      if (!res.ok) throw new Error('Failed to start migration')
      onSuccess()
      onClose()
    } catch (e) {
      console.error('Error:', e)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-8 max-w-md w-full">
        <h2 className="text-2xl font-bold text-gray-900 mb-6">Run New Migration</h2>

        <div className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Source Database</label>
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option>ABASE (us-west-2)</option>
              <option>Custom...</option>
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Target Database</label>
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option>GDS (us-east-2)</option>
              <option>Custom...</option>
            </select>
          </div>

          <div className="space-y-2">
            <label className="flex items-center">
              <input type="checkbox" defaultChecked className="mr-2" />
              <span className="text-sm text-gray-700">Auto-approve clean rows</span>
            </label>
            <label className="flex items-center">
              <input type="checkbox" defaultChecked className="mr-2" />
              <span className="text-sm text-gray-700">Create source backup</span>
            </label>
          </div>

          <div className="flex gap-3 pt-4">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg font-medium text-gray-700 hover:bg-gray-50 transition"
            >
              Cancel
            </button>
            <button
              onClick={handleRun}
              disabled={running}
              className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium transition flex items-center justify-center gap-2"
            >
              {running && <Loader2 size={18} className="animate-spin" />}
              {running ? 'Running...' : 'Run Migration'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
