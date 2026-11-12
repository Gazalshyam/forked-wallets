import React from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, toMinor, usd } from '../api.js'
import { Button, Card, ErrorText } from '../ui.jsx'

const MODES = [
  ['CONFIRMED', 'Hotel confirms'],
  ['DECLINE_FIRST', 'First hotel declines, next one confirms'],
  ['UNKNOWN', 'Hotel times out (outcome unknown)'],
]

export default function NewTask() {
  const { id } = useParams()
  const nav = useNavigate()
  const [prompt, setPrompt] = React.useState('Book a hotel in Austin for 2 nights under $200')
  const [plan, setPlan] = React.useState(null)
  const [budget, setBudget] = React.useState('')
  const [mode, setMode] = React.useState('CONFIRMED')
  const [error, setError] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  const [picked, setPicked] = React.useState(null)

  const doPlan = async () => {
    setBusy(true); setError(null)
    try {
      const p = await api(`/groups/${id}/tasks/plan`, { method: 'POST', body: { prompt } })
      setPlan(p)
      if (!picked) setPicked(p.members.map(m => m.membership_id))
      if (p.suggested_budget_minor) setBudget((p.suggested_budget_minor / 100).toFixed(2))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const start = async () => {
    const budget_minor = toMinor(budget)
    if (!budget_minor) return setError('Enter a budget like 200.00')
    setBusy(true); setError(null)
    try { const r = await api(`/groups/${id}/tasks`, { method: 'POST', body: { prompt, budget_minor, booking_mode: mode, participant_ids: picked } }); nav(`/tasks/${r.id}`) } catch (e) { setError(e.message); setBusy(false) }
  }
  const b = toMinor(budget)
  const parts = plan ? plan.members.filter(m => (picked || []).includes(m.membership_id)) : []
  const subPot = parts.reduce((a, m) => a + m.free_minor, 0)
  const over = plan && b && (b > subPot || b > plan.available_minor)

  return (
    <div className="max-w-2xl space-y-4">
      <Link to={`/groups/${id}`} className="text-indigo-700 text-sm">← Group</Link>
      <h1 className="text-2xl font-bold">⑂ New AI task</h1>
      <Card title="1. Tell the agents what you need">
        <textarea className="w-full border rounded-lg p-3 text-lg" rows="2" value={prompt} onChange={e => { setPrompt(e.target.value); setPlan(null) }} />
        <div className="mt-2 flex justify-end"><Button disabled={busy} onClick={doPlan}>Plan with AI</Button></div>
      </Card>
      {plan && (
        <Card title="2. Check the plan and fork a slice of the pot">
          <p className="text-xs text-violet-700 mb-2">✦ {plan.source}</p>
          <div className="flex flex-wrap gap-2 mb-3">
            {[['City', plan.spec.city], ['Nights', plan.spec.nights], ['Guests', plan.spec.guests], ['Min rating', plan.spec.min_rating]].map(([k, v]) => (
              <span key={k} className="bg-slate-100 rounded-full px-3 py-1 text-sm"><span className="text-slate-500">{k}:</span> <b>{v}</b></span>
            ))}
          </div>
          <label className="block text-sm">Budget (USD), confirmed by you. The AI does not set money.
            <input className="block border rounded px-2 py-1 w-32 mt-1" value={budget} onChange={e => setBudget(e.target.value)} />
          </label>
          <div className="mt-3 text-sm">
            <div className="text-slate-600 mb-1">Whose money funds this task (the sub-pot)</div>
            {plan.members.map(m => (
              <label key={m.membership_id} className="flex items-center gap-2">
                <input type="checkbox" checked={(picked || []).includes(m.membership_id)}
                  onChange={e => setPicked(e.target.checked ? [...(picked || []), m.membership_id] : (picked || []).filter(x => x !== m.membership_id))} />
                {m.name} <span className="text-slate-500">has {usd(m.free_minor)}</span>
              </label>
            ))}
          </div>
          <p className={`text-sm mt-1 ${over ? 'text-rose-700' : 'text-slate-600'}`}>Sub-pot: <b>{usd(subPot)}</b> of {usd(plan.available_minor)} available{over && ' (the budget is more than the chosen people have; pick more people or top up first)'}</p>
          <p className="text-xs text-slate-500 mt-1">Split equally; nobody pays more than they have, and the rest is shared by the others. Each person is asked first. Anyone who has not answered after the consent window is covered by your authorization; anyone who declines is left out.</p>
          <label className="block text-sm mt-3">Demo: booking outcome
            <select className="block border rounded px-2 py-1 mt-1" value={mode} onChange={e => setMode(e.target.value)}>
              {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <div className="mt-4"><Button disabled={busy || over || !b || parts.length === 0} onClick={start}>Ask {parts.length} {parts.length === 1 ? 'person' : 'people'} and fork {b ? usd(b) : ''}</Button></div>
        </Card>
      )}
      <ErrorText error={error} />
    </div>
  )
}
