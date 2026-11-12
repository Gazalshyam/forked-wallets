import React from 'react'

const LABEL_STYLES = {
  'PAYPAL SANDBOX': 'bg-blue-100 text-blue-800 border-blue-300',
  'MOCK PAYPAL': 'bg-amber-100 text-amber-800 border-amber-300',
  'SIMULATED': 'bg-violet-100 text-violet-800 border-violet-300',
}

export function SourceBadge({ label }) {
  if (!label) return null
  return (
    <span className={`inline-block text-[10px] font-bold tracking-wide px-1.5 py-0.5 rounded border ${LABEL_STYLES[label] || 'bg-slate-100 text-slate-700 border-slate-300'}`}>
      {label}
    </span>
  )
}

const STATUS_STYLES = {
  COMPLETED: 'bg-emerald-100 text-emerald-800', FUNDED: 'bg-emerald-100 text-emerald-800', RENEWED: 'bg-emerald-100 text-emerald-800',
  WON: 'bg-emerald-100 text-emerald-800', AGREED: 'bg-emerald-100 text-emerald-800', OPEN: 'bg-amber-100 text-amber-800', CONFIRMED: 'bg-emerald-100 text-emerald-800', ACTIVE: 'bg-sky-100 text-sky-800',
  FAILED: 'bg-rose-100 text-rose-800', PAUSED: 'bg-rose-100 text-rose-800', DECLINED: 'bg-rose-100 text-rose-800',
  NEEDS_REVIEW: 'bg-orange-100 text-orange-800', UNKNOWN: 'bg-orange-100 text-orange-800',
  AWAITING_CONSENT: 'bg-amber-100 text-amber-800', AGREE: 'bg-emerald-100 text-emerald-800', DECLINE: 'bg-rose-100 text-rose-800',
  OWNER_AUTHORIZED: 'bg-violet-100 text-violet-800', CANCELLED: 'bg-slate-200 text-slate-700',
  LOCKED: 'bg-indigo-100 text-indigo-800', SEARCHING: 'bg-sky-100 text-sky-800', COLLECTING: 'bg-sky-100 text-sky-800',
}

export function Status({ value }) {
  if (!value) return null
  return <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[value] || 'bg-slate-100 text-slate-700'}`}>{value}</span>
}

export function Card({ title, right, children, className = '' }) {
  return (
    <section className={`bg-white rounded-xl border border-slate-200 shadow-sm p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
          <h2 className="font-semibold text-slate-800">{title}</h2>
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function Button({ children, variant = 'primary', className = '', ...props }) {
  const styles = {
    primary: 'bg-indigo-600 hover:bg-indigo-700 text-white',
    secondary: 'bg-white hover:bg-slate-50 text-slate-800 border border-slate-300',
    danger: 'bg-rose-600 hover:bg-rose-700 text-white',
    ghost: 'text-indigo-700 hover:bg-indigo-50',
  }
  return (
    <button className={`px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed ${styles[variant]} ${className}`} {...props}>
      {children}
    </button>
  )
}

export function ErrorText({ error }) {
  if (!error) return null
  return <p className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{error}</p>
}

export function usePoll(fn, deps, ms = 1500) {
  React.useEffect(() => {
    let alive = true
    let t
    const tick = async () => {
      try { await fn() } catch { /* shown by caller */ }
      if (alive) t = setTimeout(tick, ms)
    }
    tick()
    return () => { alive = false; clearTimeout(t) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
