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

type Tab = 'catalog' | 'products' | 'campaigns'

const GOALS = ['awareness', 'conversion', 'ugc']
const PLATFORMS = ['tiktok', 'reels', 'youtube_shorts', 'facebook']

function riskColor(risk?: string): string {
  if (risk === 'high') return '#e5484d'
  if (risk === 'medium') return '#f5a623'
  return '#30a46c'
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

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    setError(''); setBusy(label)
    try { await fn() } catch (e) { setError(String(e)) } finally {
      setBusy('')
      if (workspace) loadCredits(workspace.id)  // balance may have changed
    }
  }, [workspace, loadCredits])

  if (!workspace) {
    return (
      <div className="card">
        {error ? <div className="small" style={{ color: '#e5484d' }}>{error}</div>
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
              <span><b style={{ color: credits > 0 ? '#30a46c' : '#e5484d' }}>{credits}</b> credits</span>
              {costs.video != null && <span>· analyze {costs.analyze} · concept {costs.concept} · video {costs.video}</span>}
              <BuyCredits workspaceId={workspace.id} run={run} />
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="card" style={{ borderColor: '#e5484d', marginBottom: 8 }}>
          <div className="small" style={{ color: '#e5484d' }}>{error}</div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        {(['catalog', 'products', 'campaigns'] as Tab[]).map(t => (
          <button
            key={t}
            className={t === tab ? 'primary' : 'secondary'}
            onClick={() => setTab(t)}
            style={{ textTransform: 'capitalize' }}
          >
            {t}
          </button>
        ))}
      </div>

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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
      <div className="card">
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

      <div className="card">
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
            {report.restored ? <> · <b style={{ color: '#30a46c' }}>{report.restored}</b> restored</> : null}
            {' · '}{report.item_count} total
          </div>
          {report.errors.length > 0 && (
            <ul className="small" style={{ color: '#f5a623' }}>
              {report.errors.slice(0, 8).map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      <div className="card">
        <label>Catalogs ({catalogs.length})</label>
        {catalogs.length === 0 && <div className="small">No catalogs yet — import a CSV or URLs above.</div>}
        {catalogs.map(c => (
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
          <button className="secondary small" style={{ color: '#e5484d' }} onClick={() => {
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
    <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderColor: '#3b82f6' }}>
      <span className="small">Showing items from <b>{catalogFilter.name}</b></span>
      <button className="secondary small" onClick={onClearFilter}>Show all products</button>
    </div>
  ) : null

  if (!products.length) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        {filterBanner}
        <div className="card"><div className="small">
          {catalogFilter ? 'This catalog has no items (they may have been deleted). Re-import the CSV to restore them.' : 'No products yet — import a catalog first.'}
        </div></div>
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
      <div className="card" style={{ display: 'grid', gap: 8 }}>
        <label>
          Create campaign from {analyzedSelected.length} analyzed product(s)
          {unanalyzedSelected > 0 && (
            <span className="small" style={{ color: '#f5a623' }}> · {unanalyzedSelected} unanalyzed will be skipped</span>
          )}
        </label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input placeholder="Campaign name" value={name} onChange={e => setName(e.target.value)} />
          <select value={goal} onChange={e => setGoal(e.target.value)}>
            {GOALS.map(g => <option key={g} value={g}>{g}</option>)}
          </select>
          <select value={brandId} onChange={e => setBrandId(e.target.value)}>
            <option value="">No brand</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          {PLATFORMS.map(p => (
            <label key={p} className="small" style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <input type="checkbox" checked={platforms.includes(p)} onChange={() =>
                setPlatforms(prev => prev.includes(p) ? prev.filter(x => x !== p) : [...prev, p])} />
              {p}
            </label>
          ))}
          <button className="primary" disabled={!name.trim() || !analyzedSelected.length}
            title={!analyzedSelected.length ? 'Select at least one analyzed product' : ''}
            onClick={createCampaignNow}>Create campaign →</button>
        </div>
        {!analyzedSelected.length && selected.size > 0 && (
          <div className="small" style={{ color: '#f5a623' }}>
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
    <div className="card" style={{ borderColor: selected ? '#3b82f6' : undefined }}>
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
            <button className="secondary small" onClick={() => run('Analyzing…', async () => {
              const { task_id } = await analyzeProduct(p.id)
              await pollJob(task_id)
              await onChanged()
              onAnalyzed()  // auto-select so the campaign count reflects it
            })}>{p.analysis_json ? 'Re-analyze' : 'Analyze'}</button>
            <button className="secondary small" onClick={() => setEditing(true)}>Edit</button>
            <button className="secondary small" style={{ color: '#e5484d' }} onClick={() => {
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
}) {
  const { campaigns, onChanged, run } = props
  if (!campaigns.length) {
    return <div className="card"><div className="small">No campaigns yet — select products and create one.</div></div>
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <b>{campaign.name}</b>
          <div className="small">{campaign.goal} · {(campaign.platforms || []).join(', ')} · {campaign.product_ids.length} products · {campaign.status}</div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <label className="small">variants
            <input type="number" min={1} max={10} value={nVariants} style={{ width: 56, marginLeft: 4 }}
              onChange={e => setNVariants(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} />
          </label>
          <button className="primary" onClick={() => run('Generating concepts…', async () => {
            const { tasks } = await generateCampaign(campaign.id, nVariants)
            await Promise.all(tasks.map(t => pollJob(t.task_id)))
            await onChanged(); await loadConcepts()
          })}>Generate</button>
          <button className="secondary" onClick={() => run('Loading concepts…', loadConcepts)}>View concepts</button>
          {(['json', 'csv', 'zip'] as const).map(fmt => (
            <button key={fmt} className="secondary" onClick={() => run(`Exporting ${fmt}…`, async () => {
              const blob = await downloadCampaignExport(campaign.id, fmt)
              triggerDownload(blob, `campaign_${campaign.name.replace(/\W+/g, '-').toLowerCase()}.${fmt}`)
            })}>{fmt.toUpperCase()}</button>
          ))}
        </div>
      </div>

      {concepts && (
        <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
          {concepts.length === 0 && <div className="small">No concepts yet — click Generate.</div>}
          {concepts.map(c => <ConceptCard key={c.id} concept={c} reload={loadConcepts} run={run} />)}
        </div>
      )}
    </div>
  )
}

function ConceptCard(props: {
  concept: AdConcept
  reload: () => Promise<void>
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
    <div className="card" style={{ background: '#0000000a' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600 }}>{c.hook || '(no hook)'}</div>
          <div className="small">v{c.variant_index + 1} · {c.angle || '—'}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <span className="small" style={{ color: c.status === 'flagged' ? '#e5484d' : '#30a46c' }}>{c.status}</span>
          {risk && <div className="small">risk: <span style={{ color: riskColor(risk) }}>{risk}</span></div>}
          <div style={{ display: 'flex', gap: 4, marginTop: 4, justifyContent: 'flex-end' }}>
            <button className="secondary small" onClick={() => { setEditing(e => !e); setOpen(true) }}>
              {editing ? 'Cancel' : 'Edit'}
            </button>
            <button className="secondary small" onClick={() => run('Regenerating…', async () => {
              const { task_id } = await regenerateConcept(c.id, c.angle ?? undefined)
              await pollJob(task_id)
              await reload()
            })}>Regenerate</button>
            <button className="secondary small" onClick={() => {
              if (!window.confirm('Delete this concept?')) return
              run('Deleting…', async () => { await deleteConcept(c.id); await reload() })
            }} style={{ color: '#e5484d' }}>Delete</button>
            <button className="secondary small" onClick={() => setOpen(v => !v)}>{open ? 'Hide' : 'Details'}</button>
          </div>
          <div style={{ display: 'flex', gap: 4, marginTop: 4, justifyContent: 'flex-end' }}>
            <select value={vidPlatform} onChange={e => setVidPlatform(e.target.value)} className="small">
              {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <button className="secondary small" onClick={() => run('Rendering video…', async () => {
              const { task_id } = await generateConceptVideo(c.id, vidPlatform)
              await pollJob(task_id, () => {})
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
            <div style={{ color: '#e5484d' }}>
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
  run: (label: string, fn: () => Promise<void>) => Promise<void>
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
