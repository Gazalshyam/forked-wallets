import React from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, usd } from '../api.js'
import { Card, ErrorText, SourceBadge, usePoll } from '../ui.jsx'

const SIGN = { CONTRIBUTION: '+', SPEND: '−', FORK: '⑂', MERGE_BACK: '↩', REFUND: '↶', WRITE_OFF: '⇄' }
const COLOR = { CONTRIBUTION: 'text-emerald-700', SPEND: 'text-rose-700', FORK: 'text-indigo-700', MERGE_BACK: 'text-sky-700', REFUND: 'text-orange-700', WRITE_OFF: 'text-slate-500' }

export default function Ledger() {
  const { id } = useParams()
  const [data, setData] = React.useState(null)
  const [error, setError] = React.useState(null)
  usePoll(async () => { try { setData(await api(`/groups/${id}/ledger`)); setError(null) } catch (e) { setError(e.message) } }, [id], 2000)
  if (!data) return <><ErrorText error={error} /><p className="text-slate-500">Loading…</p></>
  const p = data.pot
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to={`/groups/${id}`} className="text-indigo-700 text-sm">← {data.group.name}</Link>
        <h1 className="text-2xl font-bold">Ledger</h1>
      </div>
      <p className="text-sm text-slate-600">Append-only. Every dollar in and out. Pot numbers below are computed from these rows.</p>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-center">
        {[['Collected', p.collected_minor], ['Spent', p.spent_minor], ['Refunded', p.refunded_minor], ['Reserved (forks)', p.reserved_minor], ['Merged back', p.merged_back_minor], ['Available', p.available_minor]].map(([k, v]) => (
          <div key={k} className="bg-white border rounded-lg p-2"><div className="text-xs text-slate-500">{k}</div><div className="font-semibold">{usd(v)}</div></div>
        ))}
      </div>
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500 border-b">
              <tr><th className="py-2 pr-2">#</th><th className="pr-2">Time</th><th className="pr-2">Type</th><th className="pr-2 text-right">Amount</th><th className="pr-2">Source</th><th className="pr-2">Description</th><th>PayPal IDs</th></tr>
            </thead>
            <tbody>
              {data.entries.length === 0 && <tr><td colSpan="7" className="py-6 text-center text-slate-400">No entries yet.</td></tr>}
              {data.entries.map(e => (
                <tr key={e.id} className="border-b last:border-0 align-top">
                  <td className="py-2 pr-2 text-slate-400">{e.id}</td>
                  <td className="pr-2 whitespace-nowrap text-slate-500">{new Date(e.created_at).toLocaleTimeString()}</td>
                  <td className={`pr-2 font-semibold ${COLOR[e.entry_type]}`}>{SIGN[e.entry_type]} {e.entry_type}</td>
                  <td className="pr-2 text-right font-mono">{usd(e.amount_minor)}</td>
                  <td className="pr-2"><SourceBadge label={e.source_label} /></td>
                  <td className="pr-2">{e.description}{e.member && <span className="text-slate-500"> · {e.member}</span>}{e.task_id && <Link className="text-indigo-700 ml-1" to={`/tasks/${e.task_id}`}>task #{e.task_id}</Link>}
                    {e.allocation && <div className="text-[11px] text-slate-500">whose money: {Object.entries(e.allocation).map(([n, v]) => `${n} ${usd(v)}`).join(' · ')}</div>}</td>
                  <td className="font-mono text-xs text-slate-600 space-y-0.5">
                    {e.paypal_order_id && <div>order {e.paypal_order_id}</div>}
                    {e.paypal_capture_id && <div>capture {e.paypal_capture_id}</div>}
                    {e.paypal_payout_batch_id && <div>payout {e.paypal_payout_batch_id}</div>}
                    {e.paypal_refund_id && <div>refund {e.paypal_refund_id}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
