import Markdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CitationChip } from '@/components/CitationChip'

// The backend writes citations as markdown links with a cite: href. Rendering
// them as CitationChips here keeps the file-vs-link distinction in one place —
// the link's own citation record decides the variant, never the path text.
export function AnswerBody({ text, citations = [], streaming }) {
  const byId = new Map(citations.map((c) => [c.id, c]))
  return (
    <div className="md-content">
      <Markdown
        remarkPlugins={[remarkGfm]}
        // react-markdown strips URL schemes it does not know, which silently
        // turned every cite: citation into an unstyled plain link. Allow that
        // one scheme through; everything else keeps the default sanitiser.
        urlTransform={(url, ...rest) => (url.startsWith('cite:') ? url : defaultUrlTransform(url, ...rest))}
        components={{
          a: ({ href, children, ...props }) => {
            const cite = href?.startsWith('cite:') && byId.get(href.slice(5))
            if (!cite) return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>
            return <CitationChip label={cite.label} type={cite.type} href={cite.url ?? '#'} />
          },
        }}
      >
        {text}
      </Markdown>
      {streaming && <span className="caret" />}
    </div>
  )
}
