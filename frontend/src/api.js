// Plain fetch wrapper. Cookies carry the session.
export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`/api/v1${path}`, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await res.json() } catch { data = null }
  if (!res.ok) {
    const detail = data && data.detail
    const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(d => d.msg).join('; ') : `HTTP ${res.status}`
    const err = new Error(msg)
    err.status = res.status
    throw err
  }
  return data
}

export function usd(minor) {
  if (minor === null || minor === undefined) return '—'
  const neg = minor < 0
  const v = Math.abs(minor)
  const s = `$${Math.floor(v / 100).toLocaleString('en-US')}.${String(v % 100).padStart(2, '0')}`
  return neg ? `-${s}` : s
}

// Parse "12.34" into integer cents without floats.
export function toMinor(text) {
  const m = String(text).trim().replace(/^\$/, '').match(/^(\d+)(?:\.(\d{1,2}))?$/)
  if (!m) return null
  return parseInt(m[1], 10) * 100 + parseInt((m[2] || '0').padEnd(2, '0'), 10)
}
