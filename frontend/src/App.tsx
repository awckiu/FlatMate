import { useState, type FormEvent } from 'react'

type Flag = {
  kind: 'clarification' | 'legal' | 'clarify' | 'contradicts_law'
  clause_ref: string
  clause_excerpt: string
  explanation: string
  citation: { source: string; section: string; url: string } | null
}

type NeedToKnow = {
  topic: string
  clause_ref: string
  text: string
}

type AnalyzeResponse = {
  tenant_name: string
  co_tenants: string[]
  property_address: string
  filename: string
  pages: number
  summary: string
  need_to_knows: NeedToKnow[]
  flags: Flag[]
  email: string
  mode: 'placeholder' | 'gemini'
  law_data_available: boolean
  disclaimer: string
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [tenantName, setTenantName] = useState('')
  const [coTenants, setCoTenants] = useState('')
  const [address, setAddress] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setLoading(true)
    setError(null)
    setResult(null)

    const body = new FormData()
    body.append('agreement', file)
    body.append('lead_tenant', tenantName)
    body.append('co_tenants', coTenants)
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
          Co-tenant names (optional, comma-separated)
          <input value={coTenants} onChange={(e) => setCoTenants(e.target.value)} />
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
          <h2>Agreement summary</h2>
          <p>{result.summary}</p>
          <p>
            {result.filename}: {result.pages} page(s)
          </p>
          <h3>Need-to-knows</h3>
          {result.need_to_knows.length > 0 ? (
            <ul>
              {result.need_to_knows.map((item, i) => (
                <li key={i}>
                  <strong>{item.topic}</strong>: {item.text}
                  {item.clause_ref && ` (Clause ${item.clause_ref})`}
                </li>
              ))}
            </ul>
          ) : <p>No key details were returned.</p>}
          <h3>Flags for {result.property_address}</h3>
          <ul>
            {result.flags.map((f, i) => (
              <li key={i} className={`flag flag-${f.kind === 'contradicts_law' || f.kind === 'legal' ? 'legal' : 'clarification'}`}>
                <strong>{f.kind === 'contradicts_law' || f.kind === 'legal' ? 'Possible legal issue' : 'Ask your landlord'}</strong>
                {' · '}
                {f.clause_ref ? `Clause ${f.clause_ref}` : f.clause_excerpt}: {f.explanation}
                {f.citation && (
                  <em>
                    {' '}
                    ({f.citation.source} {f.citation.section}
                    {f.citation.url && <> · <a href={f.citation.url} target="_blank" rel="noreferrer">Source</a></>})
                  </em>
                )}
              </li>
            ))}
          </ul>
          <p>{result.disclaimer}</p>
          {result.mode === 'placeholder' && <p>Gemini is not configured; this document was not analyzed.</p>}
          {result.mode === 'gemini' && !result.law_data_available && (
            <p>No relevant law reference sections were available, so legal issues could not be checked.</p>
          )}
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
