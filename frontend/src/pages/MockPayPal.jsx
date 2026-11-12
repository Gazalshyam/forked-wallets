import React from 'react'
import { useSearchParams } from 'react-router-dom'
import { Button, SourceBadge } from '../ui.jsx'

// Stand-in for the PayPal approval page when PAYMENT_PROVIDER=mock. Clearly labelled; no real PayPal involved.
export default function MockPayPal() {
  const [params] = useSearchParams()
  const token = params.get('token')
  const amount = params.get('amount')
  const go = (path) => { window.location.href = `/api/v1/paypal/${path}?token=${encodeURIComponent(token)}&PayerID=MOCKPAYER` }
  return (
    <div className="max-w-sm mx-auto mt-12 bg-white border-2 border-amber-300 rounded-2xl shadow p-6 space-y-4 text-center">
      <SourceBadge label="MOCK PAYPAL" />
      <h1 className="text-xl font-bold">Mock PayPal checkout</h1>
      <p className="text-slate-600 text-sm">This page stands in for PayPal's approval screen because the app is running with <code>PAYMENT_PROVIDER=mock</code>. No PayPal account is contacted.</p>
      <div className="text-3xl font-bold">${amount} <span className="text-base text-slate-500">USD</span></div>
      <div className="text-xs text-slate-400 font-mono">order {token}</div>
      {token && token.startsWith('MOCK-V-') && <p className="text-xs text-slate-600">You are also agreeing to save this mock PayPal account for future automatic payments.</p>}
      <div className="flex gap-2">
        <Button className="flex-1" onClick={() => go('return')}>Approve and pay</Button>
        <Button className="flex-1" variant="secondary" onClick={() => go('cancel')}>Cancel</Button>
      </div>
    </div>
  )
}
