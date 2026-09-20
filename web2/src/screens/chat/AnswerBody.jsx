import Markdown, { defaultUrlTransform } from 'react-markdown'
import { useState } from 'react'
import remarkGfm from 'remark-gfm'
import { CitationChip } from '@/components/CitationChip'
import { SourceViewer } from '../connect/Sources'
import { externalAnswerHref } from './answerLinks'

// The backend writes citations as markdown links with a cite: href. Rendering
// them as CitationChips here keeps the file-vs-link distinction in one place —
// the link's own citation record decides the variant, never the path text.
export function AnswerBody({ text, citations = [], streaming }) {
  const [viewing, setViewing] = useState(null)
  const byId = new Map(citations.map((c) => [c.id, c]))
  return (
    <div>
    <div className="md-content chat-answer">
      <Markdown
        remarkPlugins={[remarkGfm]}
        // react-markdown strips URL schemes it does not know, which silently
        // turned every cite: citation into an unstyled plain link. Allow that
        // one scheme through; everything else keeps the default sanitiser.
        urlTransform={(url, ...rest) => (url.startsWith('cite:') ? url : defaultUrlTransform(url, ...rest))}
        components={{
          a: ({ href, children, ...props }) => {
            const cite = href?.startsWith('cite:') && byId.get(href.slice(5))
            if (!cite && href?.startsWith('cite:')) return <span title="Source citation is still being resolved">{children}</span>
            if (!cite) {
              const external = externalAnswerHref(href, window.location.origin)
              return external ? <a href={external} target="_blank" rel="noreferrer" {...props}>{children}</a>
                : <span>{children}</span>
            }
            return <CitationChip label={cite.label} type={cite.type} href={cite.url ?? '#'}
              onOpen={cite.path ? (event) => { event.preventDefault(); setViewing(cite) } : undefined} />
          },
        }}
      >
        {text}
      </Markdown>
      {streaming && <span className="caret" />}
    </div>
    {viewing && <div className="mt-3"><SourceViewer source={{ id: viewing.id, name: viewing.label, path: viewing.path, etag: viewing.etag }}
      onClose={() => setViewing(null)} /></div>}
    </div>
  )
}
