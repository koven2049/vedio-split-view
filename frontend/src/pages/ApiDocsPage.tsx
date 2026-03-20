import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Copy, Check, ChevronDown, ChevronRight, BookOpen } from 'lucide-react'
import { api } from '../lib/api'

interface Param {
  name: string
  in: string
  type: string
  description: string
}

interface Endpoint {
  method: string
  path: string
  description: string
  params: Param[]
  response: string
}

interface DocGroup {
  group: string
  endpoints: Endpoint[]
}

interface DocsData {
  auth_header: string
  base_url: string
  groups: DocGroup[]
}

const METHOD_COLORS: Record<string, string> = {
  GET: '#22c55e',
  POST: '#3b82f6',
  PUT: '#f59e0b',
  DELETE: '#ef4444',
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="p-1.5 rounded-md hover:opacity-80 transition-opacity"
      style={{ color: copied ? 'var(--color-success)' : 'var(--color-text-secondary)' }}
      title="Copy"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

function buildCurlExample(ep: Endpoint, authHeader: string): string {
  const bodyParams = ep.params.filter((p) => p.in === 'body')
  const queryParams = ep.params.filter((p) => p.in === 'query')

  let url = ep.path
  if (queryParams.length > 0) {
    const qs = queryParams.map((p) => `${p.name}=<${p.type}>`).join('&')
    url += `?${qs}`
  }

  let cmd = `curl -X ${ep.method} "https://<host>${url}"`
  cmd += `\n  -H "${authHeader}"`

  if (bodyParams.length > 0) {
    cmd += `\n  -H "Content-Type: application/json"`
    const body: Record<string, string> = {}
    bodyParams.forEach((p) => {
      body[p.name] = `<${p.type}>`
    })
    cmd += `\n  -d '${JSON.stringify(body)}'`
  }

  return cmd
}

function EndpointCard({ ep, authHeader }: { ep: Endpoint; authHeader: string }) {
  const [expanded, setExpanded] = useState(false)
  const curlExample = buildCurlExample(ep, authHeader)

  const fullSnippet = [
    `# ${ep.description}`,
    curlExample,
    '',
    `# Response:`,
    ep.response,
  ].join('\n')

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid var(--color-border)', background: 'var(--color-bg)' }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:opacity-90 transition-opacity"
      >
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span
          className="text-xs font-bold px-2 py-0.5 rounded"
          style={{ background: METHOD_COLORS[ep.method] || '#888', color: '#fff' }}
        >
          {ep.method}
        </span>
        <code className="text-sm font-mono flex-1">{ep.path}</code>
        <CopyButton text={fullSnippet} />
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3" style={{ borderTop: '1px solid var(--color-border)' }}>
          <p className="text-sm pt-3" style={{ color: 'var(--color-text-secondary)' }}>
            {ep.description}
          </p>

          {ep.params.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                Parameters
              </h4>
              <div className="space-y-1">
                {ep.params.map((p) => (
                  <div key={p.name} className="flex items-baseline gap-2 text-sm">
                    <code className="font-mono text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--color-bg-tertiary)' }}>
                      {p.name}
                    </code>
                    <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                      ({p.in}, {p.type})
                    </span>
                    <span className="text-xs">{p.description}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-1">
              <h4 className="text-xs font-semibold uppercase" style={{ color: 'var(--color-text-secondary)' }}>
                Example (curl)
              </h4>
              <CopyButton text={curlExample} />
            </div>
            <pre
              className="text-xs p-3 rounded-lg overflow-x-auto font-mono whitespace-pre-wrap"
              style={{ background: 'var(--color-bg-tertiary)' }}
            >
              {curlExample}
            </pre>
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase mb-1" style={{ color: 'var(--color-text-secondary)' }}>
              Response
            </h4>
            <pre
              className="text-xs p-3 rounded-lg overflow-x-auto font-mono whitespace-pre-wrap"
              style={{ background: 'var(--color-bg-tertiary)' }}
            >
              {ep.response}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ApiDocsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['api-docs'],
    queryFn: () => api.get<DocsData>('/docs-data'),
  })

  if (isLoading || !data) {
    return <div className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>Loading docs...</div>
  }

  const allEndpoints = data.groups.flatMap((g) =>
    g.endpoints.map((ep) => buildCurlExample(ep, data.auth_header))
  ).join('\n\n---\n\n')

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <BookOpen size={22} /> API Documentation
        </h1>
        <CopyButton text={allEndpoints} />
      </div>

      <div
        className="p-4 rounded-lg text-sm space-y-2"
        style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
      >
        <p className="font-medium">Authentication</p>
        <p style={{ color: 'var(--color-text-secondary)' }}>
          For scripts and external tools, add your API token with the following header:
        </p>
        <div className="flex items-center gap-2">
          <code
            className="text-xs px-3 py-1.5 rounded font-mono flex-1"
            style={{ background: 'var(--color-bg-tertiary)' }}
          >
            {data.auth_header}
          </code>
          <CopyButton text={data.auth_header} />
        </div>
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          Create tokens in <strong>Settings → API Tokens</strong>.
        </p>
      </div>

      {data.groups.map((group) => (
        <div key={group.group} className="space-y-2">
          <h2 className="text-lg font-semibold">{group.group}</h2>
          <div className="space-y-2">
            {group.endpoints.map((ep) => (
              <EndpointCard key={`${ep.method}-${ep.path}`} ep={ep} authHeader={data.auth_header} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
