import { useEffect, useState, type FormEvent } from 'react'

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

type BillEstimateItem = {
  id: string
  name: string
  monthly_cost: number
  included_in_rent: boolean
  active: boolean
  description: string
}

type BillsEstimate = {
  property_address: string
  postcode: string
  region: string
  tenant_count: number
  is_student: boolean
  estimate_basis: string
  live_data_available: boolean
  epc: {
    available: boolean
    description: string
    search_url: string
  }
  bills: BillEstimateItem[]
  totals: {
    total_monthly: number
    per_person_monthly: number
    student_savings_monthly: number
    currency: string
  }
  council_tax_letter: string
}

type AnalyzeResponse = {
  tenant_name: string
  co_tenants: string[]
  property_address: string
  filename: string
  review_id?: string
  case_code?: string
  pages: number
  summary: string
  need_to_knows: NeedToKnow[]
  flags: Flag[]
  email: string
  mode: 'placeholder' | 'gemini'
  law_data_available: boolean
  disclaimer: string
  household_mode: 'single' | 'friends'
  hmo_status: 'yes' | 'no' | 'unknown' | 'not_applicable'
  bills_estimate?: BillsEstimate
}

export default function App() {
  const [file, setFile] = useState<File | null>(null)
  const [tenantName, setTenantName] = useState('')
  const [coTenants, setCoTenants] = useState('')
  const [address, setAddress] = useState('')
  const [householdMode, setHouseholdMode] = useState<AnalyzeResponse['household_mode']>('single')
  const [hmoStatus, setHmoStatus] = useState<AnalyzeResponse['hmo_status']>('unknown')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalyzeResponse | null>(null)
  const [geminiConfigured, setGeminiConfigured] = useState(true)
  const [reviewCode, setReviewCode] = useState('')

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
    body.append('lead_tenant', tenantName)
    body.append('co_tenants', coTenants)
    body.append('property_address', address)
    body.append('household_mode', householdMode)
    body.append('hmo_status', householdMode === 'single' ? 'not_applicable' : hmoStatus)

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

  async function openSavedReview() {
    const code = reviewCode.trim()
    if (!code) {
      setError('Enter your agreement review code.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/cases/${encodeURIComponent(code)}`)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? `Could not open the review (${res.status}).`)
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open the saved review.')
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

      <div>
        <label>
          Already have an agreement review code?
          <input
            value={reviewCode}
            onChange={(e) => setReviewCode(e.target.value)}
            placeholder="e.g. FLAT-AB23CDEFGH"
            autoCapitalize="characters"
          />
        </label>
        <button type="button" onClick={openSavedReview} disabled={loading || !reviewCode.trim()}>
          Open saved review
        </button>
      </div>

      <form onSubmit={onSubmit}>
        <label>
          Who will you live with?
          <select
            value={householdMode}
            onChange={(e) => {
              const nextMode = e.target.value as AnalyzeResponse['household_mode']
              setHouseholdMode(nextMode)
              if (nextMode === 'friends' && hmoStatus === 'not_applicable') setHmoStatus('unknown')
            }}
          >
            <option value="single">I’m renting alone</option>
            <option value="friends">I’m living with friends</option>
          </select>
        </label>
        {householdMode !== 'single' && (
          <label>
            Do you know whether the property is an HMO?
            <select value={hmoStatus} onChange={(e) => setHmoStatus(e.target.value as AnalyzeResponse['hmo_status'])}>
              <option value="unknown">I’m not sure</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
        )}
          {householdMode === 'friends' && hmoStatus !== 'yes' && (
            <p className="hmo-warning" role="alert">
              You’re sharing with friends, but the property is marked as not an HMO or its status
              is unknown. It may still meet the HMO definition, and licensing rules vary by area.
              Check with your local council and landlord before relying on this status.
            </p>
          )}
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
          {result.case_code && (
            <p>
              Review code: <strong>{result.case_code}</strong>.{' '}
              <a href={`?review=${encodeURIComponent(result.case_code)}`}>Open the shareable report link</a>
            </p>
          )}
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
