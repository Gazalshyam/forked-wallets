import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, usd } from '../api.js'
import { Button, Card, ErrorText, SourceBadge, Status, usePoll } from '../ui.jsx'

const AGENT_COLORS = { 'Agent Alpha': 'border-sky-400', 'Agent Beta': 'border-fuchsia-400', 'Agent Gamma': 'border-amber-400' }
const RUN_STATUS = {
  QUEUED: ['Queued', 'text-slate-500'], SEARCHING: ['Searching…', 'text-sky-700 animate-pulse'], FOUND: ['Found a match, claiming…', 'text-indigo-700'],
  WON: ['🏆 Won the lock', 'text-emerald-700 font-bold'], LOST: ['Lost the race', 'text-slate-500'], CANCELLED: ['Stopped', 'text-slate-400'],
  NO_MATCH: ['No match', 'text-slate-500'], ERROR: ['Error', 'text-rose-700'],
}

function Step({ done, active, title, children }) {
  return (
    <div className={`rounded-xl border p-3 ${done ? 'border-emerald-300 bg-emerald-50' : active ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200 bg-white opacity-60'}`}>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  )
}

export default function AgentRace() {
  const { taskId } = useParams()
  const [t, setT] = React.useState(null)
  const [error, setError] = React.useState(null)
  usePoll(async () => { try { setT(await api(`/tasks/${taskId}`)); setError(null) } catch (e) { setError(e.message) } }, [taskId], 1000)
  if (!t) return <><ErrorText error={error} /><p className="text-slate-500">Loading…</p></>

  const card = t.cards.find(c => c.status !== 'CLOSED') || t.cards[t.cards.length - 1]
  const settled = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(t.status)
  const post = async (path, body) => { try { await api(`/tasks/${t.id}/${path}`, { method: 'POST', body }); setT(await api(`/tasks/${taskId}`)) } catch (e) { setError(e.message) } }
  const secsLeft = t.consent_deadline ? Math.max(0, Math.round((new Date(t.consent_deadline) - Date.now()) / 1000)) : 0
  const resolve = async (booked) => { try { await api(`/tasks/${t.id}/resolve`, { method: 'POST', body: { booked } }) } catch (e) { setError(e.message) } }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-start flex-wrap gap-2">
        <div>
          <Link to={`/groups/${t.group.id}`} className="text-indigo-700 text-sm">← {t.group.name}</Link>
          <h1 className="text-2xl font-bold">“{t.prompt}”</h1>
          <p className="text-sm text-slate-600">{t.spec.city} · {t.spec.nights} night(s) · {t.spec.guests} guests · rating ≥ {t.spec.min_rating} · budget {usd(t.budget_minor)}</p>
        </div>
        <Status value={t.status} />
      </div>
      <p className="text-xs bg-violet-50 border border-violet-200 text-violet-800 rounded p-2">Demo booking: hotels, card and booking are mock. No real hotel reservation is made. The hotel payment is labelled with what actually happened.</p>
      <ErrorText error={error} />

      {(t.status === 'AWAITING_CONSENT' || t.participants.length > 0) && (
        <Card title={t.status === 'AWAITING_CONSENT' ? 'Waiting for consent' : 'Whose money funds this task'}
          right={t.status === 'AWAITING_CONSENT' && <span className="text-sm text-amber-700 font-mono">{Math.floor(secsLeft / 60)}:{String(secsLeft % 60).padStart(2, '0')} left</span>}>
          <table className="text-sm">
            <tbody>
              {t.participants.map(p => (
                <tr key={p.membership_id}><td className="pr-3 font-semibold">{p.name}</td>
                  <td className="pr-3">{p.consent ? <Status value={p.consent} /> : <span className="text-xs text-slate-400">no answer yet</span>}</td>
                  <td className="font-mono">{p.allocation_minor != null ? `${usd(p.allocation_minor)} forked` : ''}</td></tr>
              ))}
              {t.declined.map(n => <tr key={n}><td className="pr-3 text-slate-400">{n}</td><td><Status value="DECLINE" /></td><td className="text-xs text-slate-400">money left out</td></tr>)}
            </tbody>
          </table>
          {t.status === 'AWAITING_CONSENT' && <p className="text-xs text-slate-500 mt-2">The agents start when everyone agrees. When the timer ends, the owner's authorization covers anyone who has not answered.</p>}
          <div className="flex gap-2 mt-2 flex-wrap">
            {t.my_consent_open && <><Button onClick={() => post('consent', { choice: 'AGREE' })}>Agree</Button><Button variant="danger" onClick={() => post('consent', { choice: 'DECLINE' })}>Decline</Button></>}
            {t.status === 'AWAITING_CONSENT' && t.is_owner && <Button variant="secondary" onClick={() => post('skip-wait')}>Demo: end the wait now (owner authorizes)</Button>}
          </div>
        </Card>
      )}

      <Card title="Pot">
        <div className="flex items-center gap-3 flex-wrap text-sm">
          <span>Available <b className="text-emerald-700 text-lg">{usd(t.pot.available_minor)}</b></span>
          <span className="text-2xl text-indigo-500">⑂</span>
          {t.status === 'AWAITING_CONSENT'
            ? <span className="px-3 py-1 rounded-full border-2 border-dashed border-slate-300 text-slate-500">slice {usd(t.budget_minor)} not forked until consent</span>
            : <span className={`px-3 py-1 rounded-full border-2 ${settled ? 'border-slate-300 text-slate-500 line-through' : 'border-indigo-500 text-indigo-700 animate-pulse'}`}>slice {usd(t.budget_minor)}</span>}
          {t.merged_back_minor !== null && <span className="text-sky-700 font-semibold">↩ {usd(t.merged_back_minor)} merged back</span>}
          <span className="text-slate-500">Reserved now {usd(t.pot.reserved_minor)}</span>
        </div>
      </Card>

      <div className="grid md:grid-cols-3 gap-3">
        {t.agents.map(a => {
          const [label, cls] = RUN_STATUS[a.status] || [a.status, '']
          return (
            <div key={a.id} className={`bg-white rounded-xl border-t-4 ${AGENT_COLORS[a.name] || 'border-slate-300'} shadow-sm p-4 ${a.status === 'WON' ? 'ring-2 ring-emerald-400' : ''}`}>
              <div className="flex justify-between"><b>{a.name}</b><span className="text-xs text-slate-400">{a.catalog} catalog</span></div>
              <div className={`text-sm mt-1 ${cls}`}>{label}{a.elapsed_ms ? <span className="text-slate-400 font-normal"> · {(a.elapsed_ms / 1000).toFixed(1)}s</span> : ''}</div>
              <ul className="mt-2 space-y-1">
                {a.candidates.map(c => (
                  <li key={c.id} className={`text-sm flex justify-between gap-2 ${c.status === 'DECLINED' ? 'line-through text-rose-600' : ''}`}>
                    <span>{c.name} <span className="text-slate-400">★{c.rating}</span></span>
                    <span className="font-mono">{usd(c.total_minor)}</span>
                  </li>
                ))}
              </ul>
              {a.reason && <p className="text-xs text-slate-600 mt-2 italic">{a.reason}</p>}
            </div>
          )
        })}
      </div>

      <div className="grid md:grid-cols-4 gap-3">
        <Step title="1 · Lock" done={!!t.winner_agent || settled} active={t.status === 'SEARCHING'}>
          {t.winner_agent ? <>Held by <b>{t.winner_agent}</b> <span className="font-mono text-xs">{t.lock_token}</span></> : t.status === 'SEARCHING' ? 'Waiting for the first valid claim…' : '—'}
        </Step>
        <Step title="2 · Mock card" done={card && card.status === 'USED'} active={card && card.status === 'ACTIVE'}>
          {card ? (
            <div className="rounded-lg bg-gradient-to-br from-slate-800 to-indigo-800 text-white p-3 font-mono text-xs space-y-0.5">
              <div className="flex justify-between"><span>FORKED WALLETS</span><SourceBadge label="SIMULATED" /></div>
              <div className="text-base tracking-widest">•••• •••• •••• {card.last4}</div>
              <div>limit {usd(card.limit_minor)} · {card.merchant_lock}</div>
              <div>single use · {card.status} · exp {new Date(card.expires_at).toLocaleTimeString()}</div>
              <div className="opacity-60">{card.reference}</div>
            </div>
          ) : '—'}
        </Step>
        <Step title="3 · Booking" done={t.booking_status === 'CONFIRMED'} active={['BOOKING', 'NEEDS_REVIEW'].includes(t.status)}>
          {t.booking_status ? <><Status value={t.booking_status} /> {t.booking_ref && <span className="font-mono text-xs">{t.booking_ref}</span>}</> : '—'}
          {t.status === 'NEEDS_REVIEW' && t.is_owner && (
            <div className="mt-2 space-y-1">
              <p className="text-xs text-orange-700">Unknown outcome: the lock and the reserved slice stay put until you check with the hotel.</p>
              <div className="flex gap-1 flex-wrap"><Button variant="secondary" onClick={() => resolve(true)}>It booked</Button><Button variant="secondary" onClick={() => resolve(false)}>It did not book</Button></div>
            </div>
          )}
        </Step>
        <Step title="4 · Pay hotel + merge back" done={t.status === 'COMPLETED'} active={t.status === 'BOOKING'}>
          {t.payout_label ? (
            <div className="space-y-1">
              <div>{usd(t.final_price_minor)} <SourceBadge label={t.payout_label} /></div>
              {t.payout_batch_id && <div className="font-mono text-xs">payout {t.payout_batch_id}</div>}
              <div className="text-xs text-slate-600">{t.payout_note}</div>
            </div>
          ) : '—'}
          {t.merged_back_minor !== null && <div className="text-sky-700 font-semibold mt-1">↩ {usd(t.merged_back_minor)} back to the pot</div>}
        </Step>
      </div>

      <Card title="Timeline" right={<Link to={`/groups/${t.group.id}/ledger`} className="text-sm text-indigo-700">Open ledger →</Link>}>
        <ol className="text-sm space-y-1">
          {t.events.map((e, i) => (
            <li key={i} className="flex gap-2"><span className="text-slate-400 font-mono text-xs w-20 shrink-0">{new Date(e.at).toLocaleTimeString()}</span><span className="text-xs uppercase text-slate-500 w-16 shrink-0">{e.kind}</span><span>{e.msg}</span></li>
          ))}
        </ol>
        {t.error && <p className="text-sm text-rose-700 mt-2">{t.error}</p>}
      </Card>
    </div>
  )
}
