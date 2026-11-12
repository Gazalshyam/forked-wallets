import React from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api.js'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import GroupPage from './pages/Group.jsx'
import LedgerPage from './pages/Ledger.jsx'
import NewTask from './pages/NewTask.jsx'
import AgentRace from './pages/AgentRace.jsx'
import MockPayPal from './pages/MockPayPal.jsx'

export const AuthCtx = React.createContext(null)

function Banner({ config }) {
  return (
    <div className="bg-slate-900 text-white text-xs text-center py-1.5 px-2">
      <b>Sandbox demo. No real money moves.</b>
      {config && <span className="opacity-80"> &nbsp;Payments: {config.payment_label} · AI: {config.llm_provider}</span>}
    </div>
  )
}

export default function App() {
  const [user, setUser] = React.useState(undefined)
  const [config, setConfig] = React.useState(null)
  const nav = useNavigate()

  React.useEffect(() => {
    api('/auth/config').then(setConfig).catch(() => {})
    api('/auth/me').then(setUser).catch(() => setUser(null))
  }, [])

  const logout = async () => { await api('/auth/logout', { method: 'POST' }); setUser(null); nav('/login') }

  if (user === undefined) return <><Banner config={config} /><p className="p-8 text-slate-500">Loading…</p></>

  return (
    <AuthCtx.Provider value={{ user, setUser, config }}>
      <Banner config={config} />
      {user && (
        <header className="bg-white border-b border-slate-200">
          <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4 flex-wrap">
            <Link to="/" className="font-bold text-indigo-700 text-lg">⑂ Forked Wallets</Link>
            <nav className="flex gap-1 text-sm">
              <NavLink to="/" end className={({ isActive }) => `px-2 py-1 rounded ${isActive ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600'}`}>Dashboard</NavLink>
            </nav>
            <div className="ml-auto flex items-center gap-3 text-sm">
              <span className="text-slate-600">Signed in as <b>{user.name}</b></span>
              <button onClick={logout} className="text-slate-500 hover:text-slate-800">Sign out</button>
            </div>
          </div>
        </header>
      )}
      <main className="max-w-6xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" /> : <Login />} />
          <Route path="/mock-paypal" element={<MockPayPal />} />
          <Route path="/" element={user ? <Dashboard /> : <Navigate to="/login" />} />
          <Route path="/groups/:id" element={user ? <GroupPage /> : <Navigate to="/login" />} />
          <Route path="/groups/:id/ledger" element={user ? <LedgerPage /> : <Navigate to="/login" />} />
          <Route path="/groups/:id/tasks/new" element={user ? <NewTask /> : <Navigate to="/login" />} />
          <Route path="/tasks/:taskId" element={user ? <AgentRace /> : <Navigate to="/login" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </AuthCtx.Provider>
  )
}
