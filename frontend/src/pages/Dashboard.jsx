import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, toMinor, usd } from '../api.js'
import { Button, Card, ErrorText, Status, usePoll } from '../ui.jsx'

export default function Dashboard() {
  const [data, setData] = React.useState(null)
  const [inbox, setInbox] = React.useState([])
  const [error, setError] = React.useState(null)
  const [form, setForm] = React.useState({ name: '', merchant_name: 'StreamFlix', price: '15.00' })
  const nav = useNavigate()

  const load = async () => {
    setData(await api('/groups'))
    try { setInbox((await api('/inbox')).items) } catch { /* inbox arrives in CP4 */ }
  }
  usePoll(load, [], 2500)

  const accept = async (id) => {
    try { const r = await api(`/invitations/${id}/accept`, { method: 'POST' }); nav(`/groups/${r.group_id}`) } catch (e) { setError(e.message) }
  }
  const create = async (e) => {
    e.preventDefault(); setError(null)
    const price_minor = toMinor(form.price)
    if (!price_minor) return setError('Enter a price like 15.00')
    try { const r = await api('/groups', { method: 'POST', body: { name: form.name, merchant_name: form.merchant_name, price_minor } }); nav(`/groups/${r.id}`) } catch (e2) { setError(e2.message) }
  }

  if (!data) return <p className="text-slate-500">Loading…</p>
  const unread = inbox.filter(n => !n.read_at)
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Your groups</h1>
      <ErrorText error={error} />
      {data.invitations.length > 0 && (
        <Card title="Invitations">
          {data.invitations.map(i => (
            <div key={i.id} className="flex items-center justify-between py-1">
              <span>You are invited to <b>{i.group_name}</b></span>
              <Button onClick={() => accept(i.id)}>Accept</Button>
            </div>
          ))}
        </Card>
      )}
      <div className="grid md:grid-cols-2 gap-4">
        {data.groups.map(g => (
          <Link key={g.id} to={`/groups/${g.id}`} className="block bg-white rounded-xl border border-slate-200 shadow-sm p-4 hover:border-indigo-400">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{g.name}</h2>
              <span className="text-xs text-slate-500">{g.role} · {g.member_count} members</span>
            </div>
            {g.subscription && (
              <p className="text-sm text-slate-600 mt-1">
                {g.subscription.merchant_name} {usd(g.subscription.price_minor)} · renews {g.subscription.next_renewal_date} <Status value={g.subscription.status} />
              </p>
            )}
            <div className="mt-3 grid grid-cols-3 gap-2 text-center">
              <div><div className="text-xs text-slate-500">Collected</div><div className="font-semibold">{usd(g.pot.collected_minor)}</div></div>
              <div><div className="text-xs text-slate-500">Reserved</div><div className="font-semibold">{usd(g.pot.reserved_minor)}</div></div>
              <div><div className="text-xs text-slate-500">Available</div><div className="font-semibold text-emerald-700">{usd(g.pot.available_minor)}</div></div>
            </div>
          </Link>
        ))}
      </div>
      {unread.length > 0 && (
        <Card title={`Inbox (${unread.length} unread)`}>
          <ul className="text-sm space-y-1">
            {unread.slice(0, 5).map(n => (
              <li key={n.id}>{n.group_id ? <Link className="text-indigo-700" to={`/groups/${n.group_id}`}>{n.title}</Link> : n.title}</li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Create a group">
        <form onSubmit={create} className="flex flex-wrap gap-2 items-end">
          <label className="text-sm">Group name<input className="block border rounded px-2 py-1" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} required /></label>
          <label className="text-sm">Subscription<input className="block border rounded px-2 py-1" value={form.merchant_name} onChange={e => setForm({ ...form, merchant_name: e.target.value })} /></label>
          <label className="text-sm">Price (USD)<input className="block border rounded px-2 py-1 w-24" value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} /></label>
          <Button type="submit">Create</Button>
        </form>
      </Card>
    </div>
  )
}
