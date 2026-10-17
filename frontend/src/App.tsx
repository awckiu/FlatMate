import { useEffect, useState, type FormEvent } from 'react'

type Flag = {
  kind: 'clarification' | 'legal'
  clause: string
  message: string
  citation: string
}

type AnalyzeResponse = {
  tenant_name: string
  property_address: string
  filename: string
  pages: number
  flags: Flag[]
  email: string
  mode: 'placeholder' | 'gemini'
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [tenantName, setTenantName] = useState('')
  const [address, setAddress] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)
  const [geminiConfigured, setGeminiConfigured] = useState(true)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((health) => setGeminiConfigured(Boolean(health.gemini_configured)))
      .catch(() => {})
  }, [])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setLoading(true)
    setError(null)
    setResult(null)

    const body = new FormData()
    body.append('agreement', file)
    body.append('tenant_name', tenantName)
    body.append('property_address', address)

    try {
      const res = await fetch('/api/analyze', { method: 'POST', body })
      if (!res.ok) throw new Error(`Server returned ${res.status}`)
      setResult(await res.json())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main>
      {!geminiConfigured && (
        <div className="key-banner" role="alert">
          <strong>Gemini API key missing:</strong> add <code>GEMINI_API_KEY</code> to{' '}
          <code>backend/.env</code> (see README), then restart the backend. Results below are
          placeholders until then.
        </div>
      )}
      <h1>Tenancy Demystifier</h1>
      <p>Upload your tenancy agreement and we'll flag what to check before you sign.</p>

      <form onSubmit={onSubmit}>
        <label>
          Tenancy agreement (PDF)
          <input
            type="file"
            accept="application/pdf"
            required
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          Lead tenant name
          <input value={tenantName} onChange={(e) => setTenantName(e.target.value)} required />
        </label>
        <label>
          Property address
          <input value={address} onChange={(e) => setAddress(e.target.value)} required />
        </label>
        <button type="submit" disabled={loading || !file}>
          {loading ? 'Analysing…' : 'Analyse agreement'}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {result && (
        <section>
          <h2>Flags for {result.property_address}</h2>
          <p>
            {result.filename}: {result.pages} page(s)
          </p>
          <ul>
            {result.flags.map((f, i) => (
              <li key={i} className={`flag flag-${f.kind}`}>
                <strong>{f.kind === 'legal' ? 'Possible legal issue' : 'Ask your landlord'}</strong>
                {' · '}
                {f.clause}: {f.message}
                {f.citation && <em> ({f.citation})</em>}
              </li>
            ))}
          </ul>
          {result.email && (
            <>
              <h2>Email to your landlord</h2>
              <textarea readOnly rows={12} value={result.email} />
            </>
          )}
        </section>
      )}
    </main>
  )
}
