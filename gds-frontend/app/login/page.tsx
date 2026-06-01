'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { FlaskConical, Eye, EyeOff, Loader2, AlertCircle } from 'lucide-react'

export default function GDSLogin() {
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)

  // Already logged in → skip to console
  useEffect(() => {
    if (localStorage.getItem('gds_auth') === 'true') router.replace('/')
  }, [router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    await new Promise(r => setTimeout(r, 500))

    if (username === 'admin' && password === 'gds123') {
      localStorage.setItem('gds_auth', 'true')
      router.replace('/')
    } else {
      setError('Invalid username or password.')
      setLoading(false)
    }
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center bg-blue-950"
      style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
    >
      {/* subtle dot-grid background */}
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage: 'radial-gradient(circle, #fff 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />

      <div className="relative w-full max-w-sm px-4">
        {/* Logo / branding */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 bg-blue-800 border border-blue-600 rounded-2xl flex items-center justify-center shadow-lg mb-4">
            <FlaskConical size={26} className="text-blue-200" strokeWidth={2} />
          </div>
          <h1 className="text-xl font-bold text-white tracking-tight">GD Screener</h1>
          <p className="text-blue-300 text-sm mt-1">Target Data Platform</p>
        </div>

        {/* Card */}
        <div className="bg-blue-900/60 backdrop-blur border border-blue-700/50 rounded-2xl shadow-2xl p-8">
          <h2 className="text-sm font-semibold text-blue-100 mb-6">Sign in to Admin Console</h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="block text-xs font-medium text-blue-300 mb-1.5">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                placeholder="admin"
                autoComplete="username"
                autoFocus
                className="w-full px-3.5 py-2.5 bg-blue-950/70 border border-blue-700 rounded-lg text-sm text-blue-50 placeholder:text-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent transition"
              />
            </div>

            {/* Password */}
            <div>
              <label className="block text-xs font-medium text-blue-300 mb-1.5">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  className="w-full px-3.5 py-2.5 pr-10 bg-blue-950/70 border border-blue-700 rounded-lg text-sm text-blue-50 placeholder:text-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent transition"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-blue-500 hover:text-blue-300 transition"
                  tabIndex={-1}
                >
                  {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div className="flex items-center gap-2 text-xs text-red-300 bg-red-950/40 border border-red-800/50 rounded-lg px-3 py-2.5">
                <AlertCircle size={13} className="shrink-0" />
                {error}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !username || !password}
              className="w-full mt-2 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:text-blue-600 text-white text-sm font-semibold rounded-lg transition flex items-center justify-center gap-2 shadow"
            >
              {loading
                ? <><Loader2 size={14} className="animate-spin" /> Signing in…</>
                : 'Sign in'
              }
            </button>
          </form>
        </div>

        {/* Demo hint */}
        <p className="text-center text-blue-700 text-xs mt-5">
          Demo credentials: <span className="text-blue-500 font-mono">admin / gds123</span>
        </p>
      </div>
    </div>
  )
}
