import React from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { AuthCtx } from '../App.jsx'
import { Button, Card, ErrorText } from '../ui.jsx'

export default function Login() {
  const { setUser, config } = React.useContext(AuthCtx)
  const nav = useNavigate()
  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [error, setError] = React.useState(null)

  const done = (u) => { setUser(u); nav('/') }
  const submit = async (e) => {
    e.preventDefault(); setError(null)
    try { done(await api('/auth/login', { method: 'POST', body: { email, password } })) } catch (err) { setError(err.message) }
  }
  const demo = async (name) => {
    setError(null)
    try { done(await api(`/auth/demo-login/${name}`, { method: 'POST' })) } catch (err) { setError(err.message) }
  }

  return (
    <div className="max-w-md mx-auto mt-10 space-y-4">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-indigo-700">⑂ Forked Wallets</h1>
        <p className="text-slate-600 mt-1">A shared pot for friends. Split subscriptions, then fork a slice for an AI agent task.</p>
      </div>
      {(!config || config.demo_mode) && (
        <Card title="Demo sign-in">
          <div className="flex gap-2">
            {['Asha', 'Ben', 'Chloe'].map(n => (
              <Button key={n} className="flex-1" onClick={() => demo(n)}>Sign in as {n}</Button>
            ))}
          </div>
          <p className="text-xs text-slate-500 mt-2">Asha owns the demo group. Password for all demo users: demo1234</p>
        </Card>
      )}
      <Card title="Sign in with email">
        <form onSubmit={submit} className="space-y-3">
          <input className="w-full border rounded-lg px-3 py-2" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} />
          <input className="w-full border rounded-lg px-3 py-2" placeholder="Password" type="password" value={password} onChange={e => setPassword(e.target.value)} />
          <ErrorText error={error} />
          <Button type="submit" className="w-full">Sign in</Button>
        </form>
      </Card>
    </div>
  )
}
