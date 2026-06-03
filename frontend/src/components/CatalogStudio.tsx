import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AdConcept, Brand, Campaign, Catalog, IngestReport, Product, Workspace,
} from '../types'
import {
  analyzeProduct, createBrand, createCampaign, createCheckout, deleteCatalog,
  deleteConcept, deleteProduct, downloadCampaignExport, downloadConceptVideo,
  generateCampaign, generateConceptVideo, getCreditPacks, getWorkspaceCredits,
  ingestUrls, jobStatus, listBrands, listCampaignConcepts, listCampaigns, listCatalogs,
  listProductConcepts, listProducts, listWorkspaces, regenerateConcept, updateCatalog,
  updateConcept, updateProduct, uploadCsvCatalog,
} from '../api'
import { ErrorRecoveryCard } from './ErrorRecoveryCard'
import { AdvancedSettingsDisclosure } from './AdvancedSettingsDisclosure'

type Tab = 'catalog' | 'products' | 'campaigns'

const GOALS = ['awareness', 'conversion', 'ugc']
const PLATFORMS = ['tiktok', 'reels', 'youtube_shorts', 'facebook']

// Goal copy for the seg-card selector — jargon-free labels + what each goal does.
const GOAL_META: Record<string, { label: string; blurb: string }> = {
  awareness: { label: 'Awareness', blurb: 'Reach + brand recognition' },
  conversion: { label: 'Conversion', blurb: 'Drive clicks + sales' },
  ugc: { label: 'UGC', blurb: 'Authentic creator style' },
}

// Human labels for platform ids (chips + selects).
const PLATFORM_LABELS: Record<string, string> = {
  tiktok: 'TikTok', reels: 'Reels', youtube_shorts: 'YT Shorts', facebook: 'Facebook',
}
const platformLabel = (id: string) => PLATFORM_LABELS[id] ?? id

// run() executes an async action under a busy label. The action receives an
// onProgress callback so long jobs (analyze / concepts / video) can surface the
// backend's live PROGRESS messages instead of a bare spinner.
type ProgressFn = (msg: string) => void
type RunFn = (label: string, fn: (onProgress: ProgressFn) => Promise<void>) => Promise<void>

// Risk → token color. Centralized so cards/badges stay consistent.
function riskColor(risk?: string): string {
  if (risk === 'high') return 'var(--color-danger)'
  if (risk === 'medium') return 'var(--color-warn)'
  return 'var(--color-success)'
}

// ── Shared primitives (Articles-Pull design language) ────────────────────────

/** Numbered flow-card header, matching the article pipeline's step cards. */
function StepHeader({ n, title }: { n: number | string; title: string }) {
  return (
    <div className="flow-step-header">
      <span className="flow-step-pill">{n}</span>
      <span className="flow-step-title">{title}</span>
    </div>
  )
}

/** Guided empty state — icon, message, optional CTA toward the next action. */
function EmptyState({ title, hint, cta }: {
  title: string
  hint?: string
  cta?: { label: string; onClick: () => void }
}) {
  return (
    <div className="picker-empty" style={{ display: 'grid', gap: 8, justifyItems: 'center', textAlign: 'center' }}>
      <div>{title}</div>
      {hint && <div className="small" style={{ color: 'var(--color-text-subtle)' }}>{hint}</div>}
      {cta && <button className="secondary settings-btn" onClick={cta.onClick}>{cta.label}</button>}
    </div>
  )
}

/**
 * Pipeline stepper for Ad Studio. Unlike the article stepper (forward-only),
 * every node is clickable so it doubles as tab navigation. `done` reflects real
 * data (catalogs/analyzed products/campaigns exist), `active` is the open tab.
 */
function AdStudioStepper({ tab, setTab, done }: {
  tab: Tab
  setTab: (t: Tab) => void
  done: Record<Tab, boolean>
}) {
  const steps: { id: Tab; label: string }[] = [
    { id: 'catalog', label: 'Catalog' },
    { id: 'products', label: 'Products' },
    { id: 'campaigns', label: 'Campaigns' },
  ]
  return (
    <nav className="stepper" aria-label="Ad Studio progress">
      <ol className="stepper-list">
        {steps.map((s, i) => {
          const state = s.id === tab ? 'active' : done[s.id] ? 'done' : 'pending'
          return (
            <li key={s.id} className={`stepper-item stepper-${state}`}>
              <button
                type="button"
                className="stepper-node"
                onClick={() => setTab(s.id)}
                aria-current={state === 'active' ? 'step' : undefined}
              >
                <span className="stepper-dot" aria-hidden>{done[s.id] && s.id !== tab ? '✓' : i + 1}</span>
                <span className="stepper-label">{s.label}</span>
              </button>
              {i < steps.length - 1 && <span className="stepper-bar" aria-hidden />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

/**
 * Active-job banner. Shows the busy label as the stage title and the latest
 * backend PROGRESS message below it (instead of a bare spinner). Announced via
 * aria-live so screen readers hear progress updates.
 */
function AdGenerationProgress({ label, msg }: { label: string; msg?: string }) {
  return (
    <div className="ad-progress card" role="status" aria-live="polite">
      <span className="spinner" aria-hidden />
      <div className="ad-progress-text">
        <div className="ad-progress-label">{label}</div>
        {msg && <div className="ad-progress-msg small">{msg}</div>}
      </div>
    </div>
  )
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Poll a Celery job until it finishes. Resolves on SUCCESS, rejects on FAILURE.
function pollJob(taskId: string, onTick?: (msg: string) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const id = window.setInterval(async () => {
      try {
        const s = await jobStatus(taskId)
        if (s.state === 'PROGRESS') onTick?.(s.meta?.msg ?? 'Working…')
        if (s.state === 'SUCCESS') { window.clearInterval(id); resolve() }
        else if (s.state === 'FAILURE') { window.clearInterval(id); reject(new Error(s.error ?? 'Job failed')) }
      } catch { /* transient — keep polling */ }
    }, 1500)
  })
}

export default function CatalogStudio() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [tab, setTab] = useState<Tab>('catalog')
  const [brands, setBrands] = useState<Brand[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [catalogs, setCatalogs] = useState<Catalog[]>([])
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [busy, setBusy] = useState('')
  const [busyMsg, setBusyMsg] = useState('')
  const [error, setError] = useState('')
  const [credits, setCredits] = useState<number | null>(null)
  const [costs, setCosts] = useState<Record<string, number>>({})
  // When set, the Products tab is scoped to one catalog ("opened" from Catalogs).
  const [catalogFilter, setCatalogFilter] = useState<{ id: string; name: string } | null>(null)

  const loadCredits = useCallback(async (wsId: string) => {
    try {
      const c = await getWorkspaceCredits(wsId)
      setCredits(c.balance)
      setCosts(c.costs)
    } catch { /* non-fatal */ }
  }, [])

  const refreshAll = useCallback(async (wsId: string) => {
    // Independent updates: a hiccup in one list must not block the others
    // (a failed campaigns fetch should never freeze the product list).
    const [b, p, c, cm] = await Promise.allSettled([
      listBrands(wsId), listProducts(wsId), listCatalogs(wsId), listCampaigns(wsId),
    ])
    if (b.status === 'fulfilled') setBrands(b.value.brands)
    if (p.status === 'fulfilled') setProducts(p.value.products)
    if (c.status === 'fulfilled') setCatalogs(c.value.catalogs)
    if (cm.status === 'fulfilled') setCampaigns(cm.value.campaigns)
    await loadCredits(wsId)
  }, [loadCredits])

  useEffect(() => {
    (async () => {
      try {
        const { workspaces } = await listWorkspaces()
        const ws = workspaces[0]
        if (ws) { setWorkspace(ws); await refreshAll(ws.id) }
      } catch (e) { setError(String(e)) }
    })()
  }, [refreshAll])

  const run = useCallback<RunFn>(async (label, fn) => {
    setError(''); setBusy(label); setBusyMsg('')
    try { await fn(m => setBusyMsg(m)) } catch (e) { setError(String(e)) } finally {
      setBusy(''); setBusyMsg('')
      if (workspace) loadCredits(workspace.id)  // balance may have changed
    }
  }, [workspace, loadCredits])

  if (!workspace) {
    return (
      <div className="card">
        {error ? <div className="small" style={{ color: 'var(--color-danger)' }}>{error}</div>
          : <div className="small">Loading workspace…</div>}
      </div>
    )
  }

  return (
    <div>
      <div className="header" style={{ marginBottom: 8 }}>
        <div>
          <div className="h1">Ad Studio</div>
          <div className="small">Catalog → Analysis → Ad concepts → Export</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="status">{busy || `${workspace.name} · ${products.length} products`}</div>
          {credits != null && (
            <div className="small" style={{ marginTop: 2, display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
              <span><b style={{ color: credits > 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>{credits}</b> credits</span>
              {costs.video != null && <span>· analyze {costs.analyze} · concept {costs.concept} · video {costs.video}</span>}
              <BuyCredits workspaceId={workspace.id} run={run} />
            </div>
          )}
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: 8 }}>
          <ErrorRecoveryCard error={error} onRetry={() => setError('')} retryLabel="Dismiss" />
        </div>
      )}

      <AdStudioStepper
        tab={tab}
        setTab={setTab}
        done={{
          catalog: catalogs.length > 0,
          products: products.some(p => p.analysis_json),
          campaigns: campaigns.length > 0,
        }}
      />

      {busy && <AdGenerationProgress label={busy} msg={busyMsg} />}

      {tab === 'catalog' && (
        <CatalogTab
          workspaceId={workspace.id} brands={brands} catalogs={catalogs}
          onBrandCreated={async () => { await refreshAll(workspace.id) }}
          onIngested={async () => { await refreshAll(workspace.id); setCatalogFilter(null); setTab('products') }}
          onOpenCatalog={(c) => { setCatalogFilter({ id: c.id, name: c.display_name || c.source_ref || 'catalog' }); setTab('products') }}
          onCatalogChanged={async () => { await refreshAll(workspace.id) }}
          run={run}
        />
      )}
      {tab === 'products' && (
        <ProductsTab
          products={products} workspaceId={workspace.id} brands={brands}
          catalogFilter={catalogFilter}
          onClearFilter={() => setCatalogFilter(null)}
          onChanged={async () => { await refreshAll(workspace.id) }}
          onCampaignCreated={async () => { await refreshAll(workspace.id); setTab('campaigns') }}
          run={run}
        />
      )}
      {tab === 'campaigns' && (
        <CampaignsTab campaigns={campaigns} run={run}
          onChanged={async () => { await refreshAll(workspace.id) }} />
      )}
    </div>
  )
}

// ── Catalog tab ──────────────────────────────────────────────────────────────

function CatalogTab(props: {
  workspaceId: string
  brands: Brand[]
  catalogs: Catalog[]
  onBrandCreated: () => Promise<void>
  onIngested: () => Promise<void>
  onOpenCatalog: (c: Catalog) => void
  onCatalogChanged: () => Promise<void>
  run: RunFn
}) {
  const { workspaceId, brands, catalogs, onBrandCreated, onIngested, onOpenCatalog, onCatalogChanged, run } = props
  const [brandId, setBrandId] = useState('')
  const [urls, setUrls] = useState('')
  const [report, setReport] = useState<IngestReport | null>(null)
  const [newBrand, setNewBrand] = useState('')
  const [prohibited, setProhibited] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className="card flow-card">
        <StepHeader n="1" title="Brand & compliance" />
        <label>Brand <span className="small">(optional — drives voice + compliance)</span></label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <select value={brandId} onChange={e => setBrandId(e.target.value)}>
            <option value="">No brand</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <input placeholder="New brand name" value={newBrand} onChange={e => setNewBrand(e.target.value)} />
          <input placeholder="Prohibited words (comma-sep)" value={prohibited} onChange={e => setProhibited(e.target.value)} />
          <button className="secondary" disabled={!newBrand.trim()} onClick={() => run('Creating brand…', async () => {
            const b = await createBrand(workspaceId, {
              name: newBrand.trim(),
              prohibited_words: prohibited.split(',').map(s => s.trim()).filter(Boolean),
            })
            setNewBrand(''); setProhibited(''); setBrandId(b.id); await onBrandCreated()
          })}>Add brand</button>
        </div>
      </div>

      <div className="card flow-card">
        <StepHeader n="2" title="Import products" />
        <label>Upload CSV catalog</label>
        <div className="small">Columns auto-detected (Shopify / Woo / Merchant exports).</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
          <input ref={fileRef} type="file" accept=".csv,text/csv" />
          <button className="primary" onClick={() => run('Importing CSV…', async () => {
            const f = fileRef.current?.files?.[0]
            if (!f) throw new Error('Choose a CSV file first')
            const r = await uploadCsvCatalog(workspaceId, f, brandId || undefined)
            setReport(r); await onIngested()
          })}>Import</button>
        </div>
      </div>

      <div className="card">
        <label>Or paste product page URLs (one per line)</label>
        <textarea rows={4} value={urls} onChange={e => setUrls(e.target.value)}
          placeholder="https://store.com/products/green-tea" />
        <button className="primary" style={{ marginTop: 6 }} onClick={() => run('Scraping URLs…', async () => {
          const list = urls.split('\n').map(s => s.trim()).filter(Boolean)
          if (!list.length) throw new Error('Paste at least one URL')
          const r = await ingestUrls(workspaceId, list, brandId || undefined)
          setReport(r); setUrls(''); await onIngested()
        })}>Scrape & import</button>
      </div>

      {report && (
        <div className="card">
          <div>
            <b>{report.created}</b> added · <b>{report.updated}</b> updated
            {report.restored ? <> · <b style={{ color: 'var(--color-success)' }}>{report.restored}</b> restored</> : null}
            {' · '}{report.item_count} total
          </div>
          {report.errors.length > 0 && (
            <ul className="small" style={{ color: 'var(--color-warn)' }}>
              {report.errors.slice(0, 8).map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="card flow-card">
        <StepHeader n="3" title={`Your catalogs${catalogs.length ? ` · ${catalogs.length}` : ''}`} />
        {catalogs.length === 0 ? (
          <EmptyState
            title="No catalogs yet"
            hint="Import a CSV export or paste product URLs above to pull in your products."
          />
        ) : catalogs.map(c => (
          <CatalogRow key={c.id} catalog={c} onOpen={() => onOpenCatalog(c)}
            onChanged={onCatalogChanged} run={run} />
        ))}
      </div>
    </div>
  )
}

function CatalogRow(props: {
  catalog: Catalog
  onOpen: () => void
  onChanged: () => Promise<void>
  run: RunFn
}) {
  const { catalog: c, onOpen, onChanged, run } = props
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(c.display_name ?? c.source_ref ?? '')
  const count = c.active_item_count ?? c.item_count

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '4px 0', borderTop: '1px solid #0000000f' }}>
      {renaming ? (
        <div style={{ display: 'flex', gap: 4, flex: 1 }}>
          <input value={name} onChange={e => setName(e.target.value)} style={{ flex: 1 }} />
          <button className="primary small" disabled={!name.trim()} onClick={() => run('Renaming catalog…', async () => {
            await updateCatalog(c.id, name.trim()); setRenaming(false); await onChanged()
          })}>Save</button>
          <button className="secondary small" onClick={() => setRenaming(false)}>Cancel</button>
        </div>
      ) : (
        <>
          <button className="secondary small" onClick={onOpen} title="Open catalog items">
            {c.display_name || c.source_ref || c.source_type}
          </button>
          <span className="small" style={{ flex: 1 }}>{count} items · {c.source_type} · {c.status}</span>
          <button className="secondary small" onClick={onOpen}>Open</button>
          <button className="secondary small" onClick={() => { setName(c.display_name ?? ''); setRenaming(true) }}>Rename</button>
          <button className="secondary small" style={{ color: 'var(--color-danger)' }} onClick={() => {
            if (!window.confirm(`Delete catalog "${c.display_name || c.source_ref}" and its ${count} item(s)? Items can be re-imported later.`)) return
            run('Deleting catalog…', async () => { await deleteCatalog(c.id); await onChanged() })
          }}>Delete</button>
        </>
      )}
    </div>
  )
}

// ── Products tab ─────────────────────────────────────────────────────────────

function ProductsTab(props: {
  products: Product[]
  workspaceId: string
  brands: Brand[]
  catalogFilter?: { id: string; name: string } | null
  onClearFilter?: () => void
  onChanged: () => Promise<void>
  onCampaignCreated: () => Promise<void>
  run: RunFn
}) {
  const { products: allProducts, workspaceId, brands, catalogFilter, onClearFilter, onChanged, onCampaignCreated, run } = props
  // Scope to one catalog when "opened" from the Catalogs tab.
  const products = catalogFilter ? allProducts.filter(p => p.catalog_id === catalogFilter.id) : allProducts
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [name, setName] = useState('')
  const [goal, setGoal] = useState('conversion')
  const [platforms, setPlatforms] = useState<string[]>(['tiktok'])
  const [brandId, setBrandId] = useState('')

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  // Auto-select a product once it finishes analyzing, so the campaign count
  // reflects it immediately (analyzing implies intent to use it).
  const select = (id: string) => setSelected(prev => new Set(prev).add(id))

  // A product is campaign-ready only once it has been analyzed.
  const isAnalyzed = (id: string) => !!products.find(p => p.id === id)?.analysis_json
  const analyzedSelected = [...selected].filter(isAnalyzed)
  const unanalyzedSelected = selected.size - analyzedSelected.length

  const filterBanner = catalogFilter ? (
    <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderColor: 'var(--color-accent)' }}>
      <span className="small">Showing items from <b>{catalogFilter.name}</b></span>
      <button className="secondary small" onClick={onClearFilter}>Show all products</button>
    </div>
  ) : null

  if (!products.length) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        {filterBanner}
        <div className="card">
          {catalogFilter ? (
            <EmptyState
              title="This catalog is empty"
              hint="Its items may have been deleted. Re-import the CSV to restore them."
              cta={onClearFilter ? { label: 'Show all products', onClick: onClearFilter } : undefined}
            />
          ) : (
            <EmptyState
              title="No products yet"
              hint="Head to the Catalog step to import a CSV or paste product URLs."
            />
          )}
        </div>
      </div>
    )
  }

  const createCampaignNow = () => {
    const ids = analyzedSelected
    if (!ids.length) return
    const ok = window.confirm(
      `Create a campaign with ${ids.length} analyzed product(s)?` +
      (unanalyzedSelected > 0 ? `\n\n${unanalyzedSelected} unanalyzed selection(s) will be skipped.` : '')
    )
    if (!ok) return
    run('Creating campaign…', async () => {
      await createCampaign(workspaceId, {
        name: name.trim(), goal, platforms,
        product_ids: ids, brand_id: brandId || null,
      })
      setName(''); setSelected(new Set()); await onCampaignCreated()
    })
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {filterBanner}
      <div className="card flow-card" style={{ display: 'grid', gap: 12 }}>
        <StepHeader n="→" title="Create a campaign" />
        <div className="small" style={{ color: 'var(--color-text-muted)' }}>
          {analyzedSelected.length
            ? `${analyzedSelected.length} analyzed product(s) selected`
            : 'Select one or more analyzed products below to start.'}
          {unanalyzedSelected > 0 && (
            <span style={{ color: 'var(--color-warn)' }}> · {unanalyzedSelected} unanalyzed will be skipped</span>
          )}
        </div>

        <div>
          <label>Campaign name</label>
          <input placeholder="e.g. Spring supplement push" value={name}
            onChange={e => setName(e.target.value)} style={{ width: '100%' }} />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8 }}>Goal</label>
          <div className="seg-group" role="radiogroup" aria-label="Campaign goal">
            {GOALS.map(g => {
              const m = GOAL_META[g]
              const active = goal === g
              return (
                <button key={g} type="button" role="radio" aria-checked={active}
                  className={`seg-card${active ? ' seg-card-active' : ''}`}
                  onClick={() => setGoal(g)}>
                  <span className="seg-card-title">{m?.label ?? g}</span>
                  <span className="seg-card-blurb">{m?.blurb ?? ''}</span>
                </button>
              )
            })}
          </div>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8 }}>Platforms</label>
          <div className="platform-chips">
            {PLATFORMS.map(p => {
              const active = platforms.includes(p)
              return (
                <button key={p} type="button" aria-pressed={active}
                  className={`platform-chip${active ? ' platform-chip-active' : ''}`}
                  onClick={() => setPlatforms(prev =>
                    prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])}>
                  {platformLabel(p)}
                </button>
              )
            })}
          </div>
        </div>

        <AdvancedSettingsDisclosure>
          <label>Brand voice <span className="small">(optional — drives tone + compliance)</span></label>
          <select value={brandId} onChange={e => setBrandId(e.target.value)} style={{ width: '100%' }}>
            <option value="">No brand</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </AdvancedSettingsDisclosure>

        <div className="actions">
          <button className="generate-btn"
            disabled={!name.trim() || !analyzedSelected.length || !platforms.length}
            title={!analyzedSelected.length ? 'Select at least one analyzed product'
              : !platforms.length ? 'Pick at least one platform' : ''}
            onClick={createCampaignNow}>Create campaign →</button>
        </div>

        {!analyzedSelected.length && selected.size > 0 && (
          <div className="small" style={{ color: 'var(--color-warn)' }}>
            None of the selected products are analyzed yet — click Analyze on a product first.
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
        {products.map(p => (
          <ProductCard
            key={p.id} product={p} selected={selected.has(p.id)}
            onToggle={() => toggle(p.id)} onChanged={onChanged} run={run}
            onAnalyzed={() => select(p.id)}
          />
        ))}
      </div>
    </div>
  )
}

function ProductCard(props: {
  product: Product
  selected: boolean
  onToggle: () => void
  onChanged: () => Promise<void>
  run: RunFn
  onAnalyzed: () => void
}) {
  const { product: p, selected, onToggle, onChanged, run, onAnalyzed } = props
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({
    title: p.title, description: p.description ?? '',
    price: p.price != null ? String(p.price) : '', category: p.category ?? '',
  })

  const save = () => run('Saving product…', async () => {
    if (!draft.title.trim()) throw new Error('Title cannot be empty')
    await updateProduct(p.id, {
      title: draft.title.trim(),
      description: draft.description || null,
      price: draft.price.trim() === '' ? null : Number(draft.price),
      category: draft.category || null,
    })
    setEditing(false); await onChanged()
  })

  return (
    <div className="card" style={{ borderColor: selected ? 'var(--color-accent)' : undefined }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
        <label style={{ display: 'flex', gap: 6, fontWeight: 600 }}>
          <input type="checkbox" checked={selected} onChange={onToggle} />
          {p.title}
        </label>
        <span className="small">{p.status}</span>
      </div>

      {editing ? (
        <div style={{ display: 'grid', gap: 4, marginTop: 6 }}>
          <input value={draft.title} placeholder="Title"
            onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} />
          <textarea rows={2} value={draft.description} placeholder="Description"
            onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
          <div style={{ display: 'flex', gap: 4 }}>
            <input value={draft.price} placeholder="Price" style={{ width: 90 }}
              onChange={e => setDraft(d => ({ ...d, price: e.target.value }))} />
            <input value={draft.category} placeholder="Category"
              onChange={e => setDraft(d => ({ ...d, category: e.target.value }))} />
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="primary small" onClick={save}>Save</button>
            <button className="secondary small" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div className="small">{p.category || '—'}{p.price != null ? ` · ${p.price} ${p.currency || ''}` : ''}</div>
          {p.analysis_json?.claim_risk && (
            <div className="small">
              risk: <span style={{ color: riskColor(p.analysis_json.claim_risk) }}>{p.analysis_json.claim_risk}</span>
              {' · '}{(p.analysis_json.ad_angles?.length ?? 0)} angles
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
            <button className="secondary small" onClick={() => run('Analyzing product…', async (onProgress) => {
              const { task_id } = await analyzeProduct(p.id)
              await pollJob(task_id, onProgress)
              await onChanged()
              onAnalyzed()  // auto-select so the campaign count reflects it
            })}>{p.analysis_json ? 'Re-analyze' : 'Analyze'}</button>
            <button className="secondary small" onClick={() => setEditing(true)}>Edit</button>
            <button className="secondary small" style={{ color: 'var(--color-danger)' }} onClick={() => {
              if (!window.confirm(`Delete "${p.title}"?`)) return
              run('Deleting product…', async () => { await deleteProduct(p.id); await onChanged() })
            }}>Delete</button>
          </div>
          {p.analysis_json?.ad_angles && p.analysis_json.ad_angles.length > 0 && (
            <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 16 }}>
              {p.analysis_json.ad_angles.slice(0, 3).map((a, i) => <li key={i}>{a.angle}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

// ── Campaigns tab ────────────────────────────────────────────────────────────

function CampaignsTab(props: {
  campaigns: Campaign[]
  onChanged: () => Promise<void>
  run: RunFn
}) {
  const { campaigns, onChanged, run } = props
  if (!campaigns.length) {
    return (
      <div className="card">
        <EmptyState
          title="No campaigns yet"
          hint="Go to the Products step, analyze a few products, select them, and create your first campaign."
        />
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {campaigns.map(c => <CampaignRow key={c.id} campaign={c} onChanged={onChanged} run={run} />)}
    </div>
  )
}

function CampaignRow(props: {
  campaign: Campaign
  onChanged: () => Promise<void>
  run: RunFn
}) {
  const { campaign, onChanged, run } = props
  const [nVariants, setNVariants] = useState(3)
  const [concepts, setConcepts] = useState<AdConcept[] | null>(null)

  const loadConcepts = useCallback(async () => {
    const { concepts } = await listCampaignConcepts(campaign.id)
    setConcepts(concepts)
  }, [campaign.id])

  return (
    <div className="card">
      <div className="campaign-head">
        <div className="campaign-head-info">
          <b>{campaign.name}</b>
          <div className="small">
            {GOAL_META[campaign.goal]?.label ?? campaign.goal}
            {' · '}{(campaign.platforms || []).map(platformLabel).join(', ')}
            {' · '}{campaign.product_ids.length} products
            {' · '}{campaign.status}
          </div>
        </div>
        <div className="campaign-head-actions">
          <label className="small variants-field">Variants
            <input type="number" min={1} max={10} value={nVariants}
              onChange={e => setNVariants(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} />
          </label>
          <button className="primary" onClick={() => run('Generating ad variants…', async (onProgress) => {
            const { tasks } = await generateCampaign(campaign.id, nVariants)
            let done = 0
            onProgress(`Generating ${tasks.length} variant${tasks.length !== 1 ? 's' : ''}…`)
            await Promise.all(tasks.map(t => pollJob(t.task_id, m => onProgress(m)).then(() => {
              done++; onProgress(`Finished ${done} of ${tasks.length} variants`)
            })))
            await onChanged(); await loadConcepts()
          })}>Generate</button>
          <button className="secondary" onClick={() => run('Loading concepts…', loadConcepts)}>View concepts</button>
          <span className="export-group">
            <span className="small export-label">Export</span>
            {(['json', 'csv', 'zip'] as const).map(fmt => (
              <button key={fmt} className="secondary small" onClick={() => run(`Exporting ${fmt.toUpperCase()}…`, async () => {
                const blob = await downloadCampaignExport(campaign.id, fmt)
                triggerDownload(blob, `campaign_${campaign.name.replace(/\W+/g, '-').toLowerCase()}.${fmt}`)
              })}>{fmt.toUpperCase()}</button>
            ))}
          </span>
        </div>
      </div>

      {concepts && (
        concepts.length === 0 ? (
          <div style={{ marginTop: 10 }}>
            <EmptyState title="No concepts yet" hint="Click Generate to create ad variants for this campaign." />
          </div>
        ) : (
          <div className="concept-grid">
            {concepts.map(c => <ConceptCard key={c.id} concept={c} reload={loadConcepts} run={run} />)}
          </div>
        )
      )}
    </div>
  )
}

// Gradient backdrops give each variant a distinct look at a glance (we don't
// render the real visual until "Make video"). Indexed by variant so V1/V2/V3
// stay visually stable across reloads.
const FRAME_GRADIENTS = [
  'linear-gradient(160deg, #7c3aed, #2563eb)',
  'linear-gradient(160deg, #db2777, #f59e0b)',
  'linear-gradient(160deg, #059669, #0ea5e9)',
  'linear-gradient(160deg, #dc2626, #7c3aed)',
  'linear-gradient(160deg, #0f172a, #475569)',
]

/**
 * Phone-style 9:16 mock of the ad: hook on top, on-screen text mid, headline +
 * CTA pill at the bottom — the same hierarchy a real vertical ad would use.
 * Purely presentational so variants can be compared side by side.
 */
function AdPreviewFrame({ concept: c }: { concept: AdConcept }) {
  const bg = FRAME_GRADIENTS[c.variant_index % FRAME_GRADIENTS.length]
  const empty = !c.hook && !c.on_screen_text && !c.headline && !c.cta
  return (
    <div className="ad-frame" style={{ background: bg }}
      role="img" aria-label={`Ad preview, variant ${c.variant_index + 1}`}>
      <div className="ad-frame-canvas">
        {empty ? (
          <div className="ad-frame-empty">Preview</div>
        ) : (
          <>
            <div className="ad-frame-top">{c.hook}</div>
            <div className="ad-frame-mid">{c.on_screen_text}</div>
            <div className="ad-frame-bottom">
              {c.headline && <div className="ad-frame-headline">{c.headline}</div>}
              {c.cta && <div className="ad-frame-cta">{c.cta}</div>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function ConceptCard(props: {
  concept: AdConcept
  reload: () => Promise<void>
  run: RunFn
}) {
  const { concept: c, reload, run } = props
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [vidPlatform, setVidPlatform] = useState('tiktok')
  const [hasVideo, setHasVideo] = useState(false)
  // Local draft of the editable fields.
  const [draft, setDraft] = useState({
    hook: c.hook ?? '', headline: c.headline ?? '', cta: c.cta ?? '',
    on_screen_text: c.on_screen_text ?? '', angle: c.angle ?? '',
    ugc: c.script_json?.ugc ?? '', demo: c.script_json?.demo ?? '', influencer: c.script_json?.influencer ?? '',
  })
  const risk = c.compliance_json?.risk

  const save = (recheck: boolean) => run(recheck ? 'Saving + rechecking…' : 'Saving…', async () => {
    await updateConcept(c.id, {
      hook: draft.hook, headline: draft.headline, cta: draft.cta,
      on_screen_text: draft.on_screen_text, angle: draft.angle,
      script_json: { ugc: draft.ugc, demo: draft.demo, influencer: draft.influencer },
      recheck,
    })
    setEditing(false)
    await reload()
  })

  return (
    <div className="card concept-card">
      <div className="concept-card-row">
        <AdPreviewFrame concept={c} />
        <div className="concept-meta">
          <div className="concept-badges">
            <span className="variant-badge">V{c.variant_index + 1}</span>
            <span className="small" style={{ color: c.status === 'flagged' ? 'var(--color-danger)' : 'var(--color-success)' }}>{c.status}</span>
            {risk && <span className="small">risk: <span style={{ color: riskColor(risk) }}>{risk}</span></span>}
          </div>
          <div style={{ fontWeight: 700 }}>{c.hook || '(no hook)'}</div>
          <div className="small" style={{ color: 'var(--color-text-muted)' }}>{c.angle || 'No angle'}</div>
          {c.cta && <div className="small"><b>CTA:</b> {c.cta}</div>}

          <div className="concept-actions">
            <button className="secondary small" onClick={() => { setEditing(e => !e); setOpen(true) }}>
              {editing ? 'Cancel' : 'Edit'}
            </button>
            <button className="secondary small" onClick={() => run('Regenerating variant…', async (onProgress) => {
              const { task_id } = await regenerateConcept(c.id, c.angle ?? undefined)
              await pollJob(task_id, onProgress)
              await reload()
            })}>Regenerate</button>
            <button className="secondary small" onClick={() => setOpen(v => !v)} aria-expanded={open}>
              {open ? 'Hide' : 'Details'}
            </button>
            <button className="secondary small" onClick={() => {
              if (!window.confirm('Delete this concept?')) return
              run('Deleting…', async () => { await deleteConcept(c.id); await reload() })
            }} style={{ color: 'var(--color-danger)' }}>Delete</button>
          </div>

          <div className="concept-actions">
            <select value={vidPlatform} onChange={e => setVidPlatform(e.target.value)}
              className="small" aria-label="Video platform">
              {PLATFORMS.map(p => <option key={p} value={p}>{platformLabel(p)}</option>)}
            </select>
            <button className="secondary small" onClick={() => run('Rendering video…', async (onProgress) => {
              const { task_id } = await generateConceptVideo(c.id, vidPlatform)
              await pollJob(task_id, onProgress)
              setHasVideo(true)
            })}>🎬 Make video</button>
            {hasVideo && (
              <button className="primary small" onClick={() => run('Downloading video…', async () => {
                const blob = await downloadConceptVideo(c.id)
                triggerDownload(blob, `concept_${c.id.slice(0, 8)}.mp4`)
              })}>Download MP4</button>
            )}
          </div>
        </div>
      </div>

      {open && editing && (
        <div className="small" style={{ marginTop: 8, display: 'grid', gap: 6 }}>
          {([
            ['Angle', 'angle'], ['Hook', 'hook'], ['Headline', 'headline'],
            ['CTA', 'cta'], ['On-screen text', 'on_screen_text'],
          ] as const).map(([label, key]) => (
            <label key={key}>{label}
              <input value={(draft as Record<string, string>)[key]}
                onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))}
                style={{ width: '100%' }} />
            </label>
          ))}
          {(['ugc', 'demo', 'influencer'] as const).map(k => (
            <label key={k} style={{ textTransform: 'capitalize' }}>{k} script
              <textarea rows={2} value={draft[k]}
                onChange={e => setDraft(d => ({ ...d, [k]: e.target.value }))}
                style={{ width: '100%' }} />
            </label>
          ))}
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="primary small" onClick={() => save(false)}>Save</button>
            <button className="secondary small" onClick={() => save(true)}>Save & re-check compliance</button>
          </div>
        </div>
      )}

      {open && !editing && (
        <div className="small" style={{ marginTop: 8, display: 'grid', gap: 6 }}>
          {c.headline && <div><b>Headline:</b> {c.headline}</div>}
          {c.cta && <div><b>CTA:</b> {c.cta}</div>}
          {c.script_json?.ugc && <div><b>UGC:</b> {c.script_json.ugc}</div>}
          {c.script_json?.demo && <div><b>Demo:</b> {c.script_json.demo}</div>}
          {c.script_json?.influencer && <div><b>Influencer:</b> {c.script_json.influencer}</div>}
          {c.captions_json && Object.entries(c.captions_json).map(([plat, cap]) => (
            <div key={plat}><b>{plat}:</b> {cap?.caption} {(cap?.hashtags || []).join(' ')}</div>
          ))}
          {(c.compliance_json?.flags || []).length > 0 && (
            <div style={{ color: 'var(--color-danger)' }}>
              <b>Flags:</b>
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {c.compliance_json!.flags!.map((f, i) => (
                  <li key={i}>{f.text} — {f.reason} (fix: {f.suggested_fix})</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Buy credits (Stripe Checkout) ────────────────────────────────────────────

function BuyCredits(props: {
  workspaceId: string
  run: RunFn
}) {
  const { workspaceId, run } = props
  const [packs, setPacks] = useState<{ id: string; credits: number; label: string; price_configured: boolean }[]>([])
  const [configured, setConfigured] = useState(false)
  const [pack, setPack] = useState('')

  useEffect(() => {
    getCreditPacks().then(r => {
      setConfigured(r.configured)
      setPacks(r.packs)
      const first = r.packs.find(p => p.price_configured) ?? r.packs[0]
      if (first) setPack(first.id)
    }).catch(() => {})
  }, [])

  // Hide entirely when billing isn't wired up (keeps the header clean in dev).
  if (!configured || !packs.length) return null

  return (
    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <select value={pack} onChange={e => setPack(e.target.value)} className="small">
        {packs.map(p => (
          <option key={p.id} value={p.id} disabled={!p.price_configured}>
            {p.label}{p.price_configured ? '' : ' (n/a)'}
          </option>
        ))}
      </select>
      <button className="primary small" onClick={() => run('Opening checkout…', async () => {
        const { url } = await createCheckout(workspaceId, pack)
        window.location.href = url  // redirect to Stripe-hosted Checkout
      })}>Buy credits</button>
    </span>
  )
}
