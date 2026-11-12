import React from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, toMinor, usd } from '../api.js'
import { Button, Card, ErrorText, SourceBadge, Status, usePoll } from '../ui.jsx'

function PotBar({ pot }) {
  const total = Math.max(1, pot.collected_minor)
  const pct = (v) => `${Math.max(0, Math.min(100, (v / total) * 100))}%`
  return (
    <div>
      <div className="h-4 w-full rounded-full bg-slate-100 overflow-hidden flex">
        <div className="bg-rose-400" style={{ width: pct(pot.spent_minor) }} title="Spent" />
        <div className="bg-indigo-500 animate-pulse" style={{ width: pct(pot.reserved_minor) }} title="Reserved (fork)" />
        <div className="bg-emerald-500" style={{ width: pct(pot.available_minor) }} title="Available" />
      </div>
      <div className="flex gap-4 text-xs text-slate-500 mt-1">
        <span><span className="inline-block w-2 h-2 bg-rose-400 rounded-full" /> spent</span>
        <span><span className="inline-block w-2 h-2 bg-indigo-500 rounded-full" /> forked slice</span>
        <span><span className="inline-block w-2 h-2 bg-emerald-500 rounded-full" /> available</span>
      </div>
    </div>
  )
}

const SLICE_COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#0ea5e9', '#84cc16', '#a855f7', '#f97316']

function PotPie({ shares, meId }) {
  const rows = shares.map((s, i) => ({ ...s, value: Math.max(0, s.free_minor) + Math.max(0, s.reserved_minor), color: s.active ? SLICE_COLORS[i % SLICE_COLORS.length] : '#cbd5e1' }))
  const total = rows.reduce((a, r) => a + r.value, 0)
  let angle = -Math.PI / 2
  const arcs = rows.filter(r => r.value > 0).map(r => {
    const frac = r.value / total
    const a0 = angle
    angle += frac * 2 * Math.PI
    if (frac >= 0.9999) return { ...r, full: true }
    const p = (a) => [50 + 45 * Math.cos(a), 50 + 45 * Math.sin(a)]
    const [x0, y0] = p(a0); const [x1, y1] = p(angle)
    return { ...r, d: `M50,50 L${x0},${y0} A45,45 0 ${frac > 0.5 ? 1 : 0} 1 ${x1},${y1} Z` }
  })
  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 100 100" className="w-36 h-36 shrink-0" role="img" aria-label="Each member's part of the pot">
        {total === 0 && <circle cx="50" cy="50" r="45" fill="#f1f5f9" />}
        {arcs.map(a => a.full
          ? <circle key={a.membership_id} cx="50" cy="50" r="45" fill={a.color}><title>{a.name}</title></circle>
          : <path key={a.membership_id} d={a.d} fill={a.color} stroke="white" strokeWidth="0.8"><title>{a.name}</title></path>)}
        <circle cx="50" cy="50" r="22" fill="white" />
        <text x="50" y="53" textAnchor="middle" fontSize="9" fontWeight="600" fill="#334155">{usd(total)}</text>
      </svg>
      <ul className="text-sm space-y-1.5 flex-1 min-w-[14rem]">
        {rows.map(r => (
          <li key={r.membership_id} className="flex items-start gap-2">
            <span className="inline-block w-3 h-3 rounded-sm mt-1 shrink-0" style={{ background: r.color }} />
            <span className="flex-1">
              <b>{r.name}</b>{r.membership_id === meId && ' (you)'}{!r.active && <span className="text-xs text-slate-500"> · left the group</span>}
              <span className="float-right font-mono">{usd(Math.max(0, r.free_minor) + Math.max(0, r.reserved_minor))}{total > 0 && r.value > 0 && <span className="text-slate-400 text-xs"> · {Math.round((r.value / total) * 100)}%</span>}</span>
              <div className="text-[11px] text-slate-500">
                {r.reserved_minor > 0 && <span>{usd(r.reserved_minor)} in a running task · </span>}
                {r.committed_minor > 0 && <span>{usd(r.committed_minor)} is this cycle's share (spent at renewal) · </span>}
                {r.free_minor < 0 && <span className="text-rose-700">owes {usd(-r.free_minor)} (a share was covered; repaid next cycle)</span>}
              </div>
            </span>
          </li>
        ))}
        {rows.length === 0 && <li className="text-slate-500">Nobody has paid in yet.</li>}
      </ul>
    </div>
  )
}

export default function GroupPage() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const [g, setG] = React.useState(null)
  const [inbox, setInbox] = React.useState([])
  const [error, setError] = React.useState(null)
  const [flash, setFlash] = React.useState(params.get('msg'))
  const [busy, setBusy] = React.useState(false)
  const [saveToken, setSaveToken] = React.useState(true)
  const [inviteEmail, setInviteEmail] = React.useState('')
  const [topUp, setTopUp] = React.useState('70.00')
  const [split, setSplit] = React.useState(null)
  const [confirmLeave, setConfirmLeave] = React.useState(false)

  const load = async () => {
    try {
      const d = await api(`/groups/${id}`)
      setG(d)
      try { setInbox((await api(`/inbox?group_id=${id}`)).items) } catch { /* CP4 */ }
    } catch (e) { setError(e.message) }
  }
  usePoll(load, [id], 1500)

  const act = async (fn, ok) => {
    setBusy(true); setError(null)
    try { const r = await fn(); if (ok) setFlash(typeof ok === 'function' ? ok(r) : ok); await load(); return r } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const pay = async (kind, amount_minor, covered_membership_id) => {
    const r = await act(() => api(`/groups/${id}/pay`, { method: 'POST', body: { kind, amount_minor, covered_membership_id, save_for_auto_debit: kind === 'SHARE' && saveToken } }))
    if (r && r.approval_url) window.location.href = r.approval_url
    else if (r) setFlash(r.message)
  }

  if (!g) return <><ErrorText error={error} /><p className="text-slate-500">Loading…</p></>
  const meRow = g.members.find(m => m.membership_id === g.me.membership_id)
  const owner = g.me.is_owner
  const cyc = g.cycle
  const pctCollected = cyc ? Math.min(100, (cyc.collected_minor / Math.max(1, cyc.required_minor)) * 100) : 0
  const unread = inbox.filter(n => !n.read_at)
  const myPart = (g.pot_shares || []).find(s => s.membership_id === g.me.membership_id)
  const settlement = myPart ? myPart.free_minor - myPart.committed_minor : 0
  const consent = async (taskId, choice) => act(() => api(`/tasks/${taskId}/consent`, { method: 'POST', body: { choice } }), choice === 'AGREE' ? 'You agreed; your money can be used for this task' : 'You declined; your money is left out')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <Link to="/" className="text-indigo-700 text-sm">← Dashboard</Link>
          <h1 className="text-2xl font-bold">{g.name}</h1>
          {g.subscription && (
            <p className="text-slate-600 text-sm">{g.subscription.merchant_name} · {usd(g.subscription.price_minor)} every {g.subscription.cadence_days} days · next renewal {g.subscription.next_renewal_date} <Status value={g.subscription.status} /></p>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          {!owner && (confirmLeave
            ? <span className="flex gap-1 items-center text-sm flex-wrap">Leave {g.name}?
                <span className="text-xs text-slate-600">{settlement > 0 ? `About ${usd(settlement)} goes back to your PayPal account.` : settlement < 0 ? `You owe ${usd(-settlement)}; it is written off and the group is told.` : 'Nothing to refund.'}</span>
                <Button variant="danger" disabled={busy} onClick={async () => { const r = await act(() => api(`/groups/${id}/leave`, { method: 'POST' })); if (r) window.location.href = '/' }}>Yes, leave</Button>
                <Button variant="ghost" onClick={() => setConfirmLeave(false)}>Cancel</Button></span>
            : <Button variant="ghost" onClick={() => setConfirmLeave(true)}>Leave group</Button>)}
          <Link to={`/groups/${id}/ledger`}><Button variant="secondary">Ledger</Button></Link>
          {owner && <Link to={`/groups/${id}/tasks/new`}><Button>⑂ New AI task</Button></Link>}
        </div>
      </div>

      {flash && <p className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2 flex justify-between">{flash}<button onClick={() => setFlash(null)}>✕</button></p>}
      <ErrorText error={error} />

      <Card title="Group pot" right={<span className="text-xs text-slate-500">Logical allocation in the database. Funds sit in the platform's {g.capabilities.payment_label} business account.</span>}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          {[['Collected', g.pot.collected_minor, ''], ['Spent', g.pot.spent_minor, 'text-rose-700'], ['Reserved (forks)', g.pot.reserved_minor, 'text-indigo-700'], ['Available', g.pot.available_minor, 'text-emerald-700']].map(([k, v, c]) => (
            <div key={k}><div className="text-xs text-slate-500">{k}</div><div className={`text-2xl font-bold ${c}`}>{usd(v)}</div></div>
          ))}
        </div>
        <PotBar pot={g.pot} />
      </Card>

      {g.pot_shares && (
        <Card title="Whose money is in the pot" right={<span className="text-xs text-slate-500">From the ledger: what each person paid in, minus their part of every spend and fork.</span>}>
          <PotPie shares={g.pot_shares} meId={g.me.membership_id} />
        </Card>
      )}

      {g.decision && (
        <Card title={g.decision.status === 'PAUSED' ? 'Paused by vote' : 'Group decision needed'} right={<Status value={g.decision.status} />}>
          <p className="text-sm text-slate-700 mb-2">
            <b>{g.decision.left.join(' and ')}</b> left the group. Should the remaining members pay more {g.decision.target === 'current' ? 'for this cycle' : 'from the next cycle'}, or pause {g.subscription.merchant_name}?
            {' '}The new split applies only if everyone agrees; one "Pause" vote pauses it. Until then nothing is collected at the new amounts.
          </p>
          <table className="text-sm mb-2">
            <tbody>
              {g.decision.voters.map(v => (
                <tr key={v.membership_id}><td className="pr-3 font-semibold">{v.name}</td><td className="pr-3">{usd(v.old_share_minor)} → <b>{usd(v.new_share_minor)}</b></td>
                  <td>{v.vote ? <Status value={v.vote === 'AGREE' ? 'AGREED' : 'PAUSED'} /> : <span className="text-xs text-slate-400">not voted</span>}</td></tr>
              ))}
            </tbody>
          </table>
          {g.decision.can_vote && !g.decision.my_vote && (
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => act(() => api(`/groups/${id}/decision/vote`, { method: 'POST', body: { choice: 'AGREE' } }), 'Vote recorded: agree')}>Agree to the new share</Button>
              <Button variant="danger" disabled={busy} onClick={() => act(() => api(`/groups/${id}/decision/vote`, { method: 'POST', body: { choice: 'PAUSE' } }), 'Vote recorded: pause')}>Pause the subscription</Button>
            </div>
          )}
          {g.decision.status === 'PAUSED' && owner && <Button disabled={busy} onClick={() => act(() => api(`/groups/${id}/decision/ask-again`, { method: 'POST' }), 'Asked the group again')}>Ask the group again</Button>}
        </Card>
      )}

      {unread.length > 0 && (
        <Card title={`Inbox (${unread.length})`} right={<span className="text-xs text-slate-500">In-app push. Email would also be sent (disabled in this demo).</span>}>
          <ul className="space-y-2">
            {unread.map(n => (
              <li key={n.id} className={`border rounded-lg p-3 ${n.kind === 'REMINDER' ? 'border-amber-300 bg-amber-50' : n.kind === 'COVER_REQUEST' ? 'border-sky-300 bg-sky-50' : n.kind === 'DECISION' ? 'border-violet-300 bg-violet-50' : n.kind === 'CONSENT' ? 'border-indigo-300 bg-indigo-50' : 'border-slate-200'}`}>
                <div className="flex justify-between gap-2">
                  <b className="text-sm">{n.title}</b>
                  <span className="text-xs text-slate-400">{new Date(n.created_at).toLocaleTimeString()}</span>
                </div>
                {n.body && <p className="text-sm text-slate-700 mt-1 whitespace-pre-line">{n.body}</p>}
                {n.ai_written && <p className="text-[11px] text-violet-700 mt-1">✦ Written by the Collections agent ({n.ai_written})</p>}
                <div className="flex gap-2 mt-2">
                  {n.action && n.action.type === 'PAY_SHARE' && meRow && !meRow.is_paid && <Button disabled={busy} onClick={() => pay('SHARE')}>Pay my share</Button>}
                  {n.action && n.action.type === 'COVER_SHARE' && g.members.some(m => m.membership_id === n.action.membership_id && !m.is_paid) && <Button disabled={busy} onClick={() => pay('COVER', undefined, n.action.membership_id)}>Pay {n.action.name}'s share ({usd(n.action.amount_minor)})</Button>}
                  {n.action && n.action.type === 'TASK_CONSENT' && <>
                    <Button disabled={busy} onClick={() => consent(n.action.task_id, 'AGREE')}>Agree</Button>
                    <Button variant="danger" disabled={busy} onClick={() => consent(n.action.task_id, 'DECLINE')}>Decline</Button>
                    <Link to={`/tasks/${n.action.task_id}`}><Button variant="secondary">Open task</Button></Link>
                  </>}
                  {(!n.action || n.action.type !== 'TASK_CONSENT') && <Button variant="ghost" onClick={() => act(() => api(`/inbox/${n.id}/read`, { method: 'POST' }))}>Dismiss</Button>}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {cyc && (
        <Card title={`Current cycle · renews ${cyc.renewal_date}`} right={<Status value={cyc.status} />}>
          <div className="flex items-center gap-3 mb-3">
            <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden"><div className="h-full bg-emerald-500 transition-all" style={{ width: `${pctCollected}%` }} /></div>
            <span className="text-sm font-semibold">{usd(cyc.collected_minor)} / {usd(cyc.required_minor)}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500 border-b"><tr><th className="py-1">Member</th><th>Share</th><th>Payment mode</th><th>This cycle</th><th></th></tr></thead>
              <tbody>
                {g.members.map(m => (
                  <tr key={m.membership_id} className="border-b last:border-0">
                    <td className="py-2"><b>{m.name}</b> {m.role === 'OWNER' && <span className="text-xs text-slate-500">owner</span>}{m.force_fail_next && <span className="ml-1 text-[10px] bg-rose-100 text-rose-700 px-1 rounded">next charge forced to fail (demo)</span>}</td>
                    <td>{usd(m.share_minor)}{m.share_note && <div className="text-[11px] text-sky-700 max-w-[14rem]">{m.share_note}</div>}</td>
                    <td className="text-xs">
                      {m.payment_mode === 'AUTO_DEBIT' ? 'Auto-debit' : 'Approve each cycle'}
                      {m.payment_mode === 'AUTO_DEBIT' && (m.has_saved_token ? <> · token saved <SourceBadge label={m.token_label} /></> : <span className="text-amber-700"> · no saved token yet</span>)}
                    </td>
                    <td>
                      {m.is_paid ? <Status value="COMPLETED" /> : m.last_attempt ? <span title={m.last_attempt.error || ''}><Status value={m.last_attempt.status} /> <SourceBadge label={m.last_attempt.source_label} /></span> : <span className="text-slate-400 text-xs">not started</span>}
                      {m.last_attempt && m.last_attempt.error && !m.is_paid && <div className="text-xs text-rose-700">{m.last_attempt.error}</div>}
                    </td>
                    <td className="text-right">
                      {m.membership_id === g.me.membership_id && !m.is_paid && (
                        m.last_attempt && m.last_attempt.approval_url
                          ? <a href={m.last_attempt.approval_url}><Button>Continue to PayPal</Button></a>
                          : <Button disabled={busy} onClick={() => pay('SHARE')}>Pay my share</Button>
                      )}
                    </td>
                  </tr>
                ))}
                {g.departed.map(d => (
                  <tr key={d.membership_id} className="border-b last:border-0 text-slate-400">
                    <td className="py-2">{d.name} <span className="text-xs">left the group</span></td>
                    <td>{usd(d.share_minor)}</td><td className="text-xs">no further charges</td>
                    <td className="text-xs">paid {usd(d.paid_minor)}</td><td />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {meRow && (
            <div className="mt-3 text-sm flex flex-wrap items-center gap-3 border-t pt-3">
              <span className="text-slate-600">My payment mode:</span>
              <select className="border rounded px-2 py-1" value={g.me.payment_mode} onChange={e => act(() => api(`/groups/${id}/me/payment-mode`, { method: 'PUT', body: { payment_mode: e.target.value } }))}>
                <option value="AUTO_DEBIT">Auto-debit (saved PayPal token)</option>
                <option value="APPROVAL_EACH_CYCLE">Approve each cycle</option>
              </select>
              {g.me.payment_mode === 'AUTO_DEBIT' && !meRow.is_paid && (
                <label className="flex items-center gap-1"><input type="checkbox" checked={saveToken} onChange={e => setSaveToken(e.target.checked)} /> save my PayPal for auto-debit on this payment</label>
              )}
              {!g.capabilities.auto_debit_available && <span className="text-xs text-amber-700">Auto-debit is not enabled for this PayPal app (vault off). Members approve each cycle instead.</span>}
            </div>
          )}
        </Card>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {owner && (
          <Card title="Demo controls" right={<span className="text-xs text-slate-500">Fast-forward the clock</span>}>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" disabled={busy} onClick={() => act(() => api(`/groups/${id}/demo/run/COLLECT`, { method: 'POST' }), 'COLLECT queued (D-5): auto-debits and pay requests')}>Run COLLECT (D-5)</Button>
              <Button variant="secondary" disabled={busy} onClick={() => act(() => api(`/groups/${id}/demo/run/CHECK`, { method: 'POST' }), 'CHECK queued (D-1): shortfall reminders')}>Run CHECK (D-1)</Button>
              <Button variant="secondary" disabled={busy} onClick={() => act(() => api(`/groups/${id}/demo/run/RENEW`, { method: 'POST' }), 'RENEW queued')}>Run RENEW</Button>
            </div>
            <div className="mt-3 text-sm">
              <span className="text-slate-600">Force next charge to fail:</span>
              <div className="flex flex-wrap gap-2 mt-1">
                {g.members.map(m => (
                  <Button key={m.membership_id} variant={m.force_fail_next ? 'danger' : 'secondary'} disabled={busy}
                    onClick={() => act(() => api(`/groups/${id}/demo/force-fail`, { method: 'POST', body: { membership_id: m.membership_id, value: !m.force_fail_next } }))}>
                    {m.name}{m.force_fail_next ? ' ✓' : ''}
                  </Button>
                ))}
              </div>
            </div>
            <p className="text-xs text-slate-500 mt-2">Jobs go through the worker queue and run within a couple of seconds.</p>
          </Card>
        )}

        <Card title="Top up the pot" right={<SourceBadge label={g.capabilities.payment_label} />}>
          <p className="text-sm text-slate-600 mb-2">One-off contribution for group plans (for example a trip). Paid through PayPal like a share.</p>
          <div className="flex gap-2">
            <input className="border rounded px-2 py-1 w-28" value={topUp} onChange={e => setTopUp(e.target.value)} />
            <Button disabled={busy} onClick={() => { const v = toMinor(topUp); if (!v) return setError('Enter an amount like 70.00'); pay('TOP_UP', v) }}>Top up</Button>
          </div>
        </Card>

        {owner && (
          <Card title="Members and split">
            <form className="flex gap-2 mb-3" onSubmit={e => { e.preventDefault(); act(() => api(`/groups/${id}/invitations`, { method: 'POST', body: { email: inviteEmail } }), 'Invitation sent (in-app)').then(() => setInviteEmail('')) }}>
              <input className="border rounded px-2 py-1 flex-1" placeholder="friend@example.com" value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} />
              <Button type="submit" disabled={busy}>Invite</Button>
            </form>
            {g.invitations.filter(i => i.status === 'PENDING').map(i => <div key={i.id} className="text-xs text-slate-500">Pending: {i.email}</div>)}
            <div className="text-sm mt-2">
              Split: <b>{g.subscription.split_mode}</b>
              {split === null
                ? <Button variant="ghost" onClick={() => setSplit(Object.fromEntries(g.members.map(m => [m.membership_id, ((m.share_minor || 0) / 100).toFixed(2)])))}>Edit custom split</Button>
                : (
                  <div className="mt-2 space-y-1">
                    {g.members.map(m => (
                      <label key={m.membership_id} className="flex items-center gap-2">{m.name}<input className="border rounded px-2 py-0.5 w-24" value={split[m.membership_id]} onChange={e => setSplit({ ...split, [m.membership_id]: e.target.value })} /></label>
                    ))}
                    <div className="flex gap-2">
                      <Button onClick={() => {
                        const shares = Object.fromEntries(Object.entries(split).map(([k, v]) => [k, toMinor(v)]))
                        act(() => api(`/groups/${id}/split`, { method: 'PUT', body: { mode: 'CUSTOM', shares } }), r => r.applied_to_current_cycle ? 'Custom split saved' : 'Custom split saved; applies from the next cycle').then(r => r && setSplit(null))
                      }}>Save</Button>
                      <Button variant="secondary" onClick={() => act(() => api(`/groups/${id}/split`, { method: 'PUT', body: { mode: 'EQUAL' } }), 'Equal split restored').then(() => setSplit(null))}>Use equal split</Button>
                      <Button variant="ghost" onClick={() => setSplit(null)}>Cancel</Button>
                    </div>
                  </div>
                )}
            </div>
          </Card>
        )}

        <Card title="AI tasks">
          {g.tasks.length === 0 && <p className="text-sm text-slate-500">No tasks yet. {owner && 'Fork a slice of the pot and let three agents race to book.'}</p>}
          <ul className="text-sm space-y-1">
            {g.tasks.map(t => (
              <li key={t.id}><Link className="text-indigo-700" to={`/tasks/${t.id}`}>#{t.id} {t.prompt}</Link> <Status value={t.status} /> {t.final_price_minor ? usd(t.final_price_minor) : ''}</li>
            ))}
          </ul>
        </Card>

        <Card title="Cycle history">
          <ul className="text-sm space-y-1">
            {g.cycles.map(c => <li key={c.id}>{c.renewal_date}: {usd(c.collected_minor)} / {usd(c.required_minor)} <Status value={c.status} /></li>)}
          </ul>
        </Card>
      </div>
    </div>
  )
}
