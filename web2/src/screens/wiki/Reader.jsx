import { useEffect, useMemo, useState } from 'react'
import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen, Plug, Trash2, TriangleAlert } from 'lucide-react'
import * as api from '@/api'
import { ago } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { CitationChip } from '@/components/CitationChip'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { EmptyState } from '@/components/EmptyState'
import { GradeBadgePills } from '@/components/GradeBadgePills'
import { SkeletonList } from '@/components/SkeletonList'
import { SourceRow } from '@/components/SourceRow'
import { typeColor } from './ArticleList'

// [[Title]] is the wiki's own link syntax. Rewriting it to a wiki: href lets
// the one markdown renderer below handle both link kinds; a title with no
// article behind it degrades to plain text rather than a dead link.
function linkWikilinks(body, pathByTitle) {
  return body.replace(/\[\[([^\]]+)\]\]/g, (_whole, raw) => {
    const title = raw.trim()
    const path = pathByTitle.get(title)
    return path ? `[${title}](wiki:${path})` : title
  })
}

// Same convention as the chat answer body: the backend writes citations as
// markdown links with a cite: href, and the link's own citation record decides
// the chip variant — never the path text.
function ArticleBody({ text, citations, onNavigate }) {
  const byId = useMemo(() => new Map(citations.map((c) => [c.id, c])), [citations])
  return (
    <div className="md-content">
      <Markdown
        remarkPlugins={[remarkGfm]}
        // react-markdown strips URL schemes it does not know, which would
        // silently turn every citation and wikilink into an unstyled plain
        // link. Allow those two through; everything else keeps the sanitiser.
        urlTransform={(url, ...rest) => (
          url.startsWith('cite:') || url.startsWith('wiki:') ? url : defaultUrlTransform(url, ...rest)
        )}
        components={{
          a: ({ href, children, ...props }) => {
            const cite = href?.startsWith('cite:') && byId.get(href.slice(5))
            if (cite) return <CitationChip label={cite.label} type={cite.type} href={cite.url ?? '#'} />
            if (href?.startsWith('wiki:')) {
              const path = href.slice(5)
              return (
                <a href={`#${path}`} onClick={(e) => { e.preventDefault(); onNavigate?.(path) }} {...props}>
                  {children}
                </a>
              )
            }
            return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
          },
        }}
      >
        {text}
      </Markdown>
    </div>
  )
}

function Chip({ children, style, className }) {
  return (
    <span className={`shrink-0 rounded-full border border-border px-2 py-0.5 text-[11px] ${className ?? ''}`} style={style}>
      {children}
    </span>
  )
}

function Label({ children }) {
  return <h2 className="text-[11px] font-medium uppercase tracking-[0.03em] text-muted-foreground">{children}</h2>
}

export function Reader({ path, nodes, state, absorbedAt, onNavigate, onRetry, onConnect, onDeleted }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState(null)
  const [article, setArticle] = useState(null)
  const [articleError, setArticleError] = useState(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    if (!path) { setArticle(null); setArticleError(null); return }
    let cancelled = false
    setArticle(null)
    setArticleError(null)
    api.wikiArticle({ path })
      .then((doc) => {
        if (cancelled) return
        if (doc) setArticle(doc)
        else setArticleError(new Error('not found'))
      })
      .catch((e) => !cancelled && setArticleError(e))
    return () => { cancelled = true }
  }, [path, reload])

  const pathByTitle = useMemo(() => new Map(nodes.map((n) => [n.title, n.path])), [nodes])
  const typeByPath = useMemo(() => new Map(nodes.map((n) => [n.path, n.type])), [nodes])

  if (state === 'loading') {
    return <div className="pt-6"><SkeletonList rows={7} icon={false} /></div>
  }

  if (state === 'error') {
    return (
      <EmptyState
        className="py-20"
        icon={TriangleAlert}
        title="Couldn’t load the wiki."
        detail="Nothing was lost — your articles are still absorbed."
        action={
          <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={onRetry}>
            Try again
          </Button>
        }
      />
    )
  }

  if (!nodes.length) {
    return (
      <EmptyState
        className="py-20"
        icon={BookOpen}
        title="No wiki articles yet. Once you absorb some sources, they’ll show up here."
        detail="Connect a source and run an absorb to fill the wiki."
        action={onConnect && (
          <Button variant="outline" size="sm" onClick={onConnect}>
            <Plug size={12} aria-hidden />Go to Connect
          </Button>
        )}
      />
    )
  }

  if (!path) {
    return <EmptyState className="py-20" icon={BookOpen} title="Pick an article to read it, with every source it was built on." />
  }

  if (articleError) {
    return (
      <EmptyState
        className="py-20"
        icon={TriangleAlert}
        title="Couldn’t open that article."
        detail={path}
        action={
          <Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={() => setReload((n) => n + 1)}>
            Try again
          </Button>
        }
      />
    )
  }

  if (!article) return <div className="pt-6"><SkeletonList rows={7} icon={false} /></div>

  const colour = typeColor(article.type)
  const absorbed = absorbedAt?.get(path)

  return (
    <article>
      <header className="flex flex-wrap items-center gap-2 pb-4">
        <h1 className="mr-1 text-[15px] font-semibold text-foreground">{article.title}</h1>
        <Chip style={{ borderColor: colour, color: colour, background: `${colour}14` }}>{article.type}</Chip>
        {article.stale && <Chip className="border-warning/40 text-warning">stale — pending re-verification</Chip>}
        <GradeBadgePills grades={article.grades} className="ml-auto" />
        {/* Deleting un-writes the article and puts its source back in the
            queue. It does not un-scrape the file, which is why the confirm
            says so — "delete" on a wiki page reads more final than it is. */}
        <Button
          variant="ghost" size="xs" className="text-muted-foreground hover:text-destructive"
          onClick={() => { setDeleteError(null); setConfirmingDelete(true) }}
        >
          <Trash2 size={12} aria-hidden /> Delete
        </Button>
      </header>

      {deleteError && (
        <p className="mb-3 flex items-start gap-1.5 text-[12.5px] text-destructive">
          <TriangleAlert size={14} className="mt-[2px] shrink-0" aria-hidden />
          <span>{deleteError}</span>
        </p>
      )}

      <ConfirmDialog
        open={confirmingDelete}
        title={`Delete “${article.title}”?`}
        detail={'The article goes and its source returns to the write-up queue, so '
              + 'the next write-up produces it again. The scraped file itself is '
              + 'untouched.'}
        confirmLabel="Delete"
        onConfirm={async () => {
          setConfirmingDelete(false)
          try {
            await api.deleteArticle({ path })
            onDeleted?.(path)
          } catch (e) {
            setDeleteError(e.message)
          }
        }}
        onCancel={() => setConfirmingDelete(false)}
      />

      <ArticleBody
        text={linkWikilinks(article.body ?? '', pathByTitle)}
        citations={article.citations ?? []}
        onNavigate={onNavigate}
      />

      {article.related?.length > 0 && (
        <section className="mt-7 border-t border-border pt-4">
          <Label>Related</Label>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {article.related.map((r) => (
              <button
                key={r.path}
                type="button"
                onClick={() => onNavigate?.(r.path)}
                className="flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-foreground hover:border-primary"
              >
                <span className="size-2 rounded-full" style={{ background: typeColor(typeByPath.get(r.path)) }} aria-hidden />
                {r.title}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* The article-level audit trail. It stays inline at every width — hiding
          it behind a tab is what makes an answer unverifiable. */}
      <section className="mt-7 border-t border-border pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <Label>Sources<span className="ml-1 opacity-70">{article.sources?.length ?? 0}</span></Label>
          <span className="text-[11.5px] text-muted-foreground">
            {absorbed ? `absorbed ${ago(absorbed)}` : 'absorb time not recorded'}
          </span>
        </div>
        <p className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
          Everything this article was built on, with the time each source was last scraped.
          A web page opens its real original URL, not the cached copy.
        </p>

        {article.sources?.length ? (
          <div className="mt-1 divide-y divide-border">
            {article.sources.map((source) => (
              <SourceRow
                key={source.id}
                source={source}
                onOpenOriginal={(s) => s.url && window.open(s.url, '_blank', 'noreferrer')}
              />
            ))}
          </div>
        ) : (
          <p className="py-3 text-[13px] text-muted-foreground">
            No sources recorded for this article yet — it was written before source tracking, or its absorb is still pending.
          </p>
        )}
      </section>
    </article>
  )
}
