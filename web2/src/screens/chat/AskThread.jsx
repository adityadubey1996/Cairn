import { MessageSquare, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'
import { TrustLine } from '@/components/TrustLine'
import { AnswerBody } from './AnswerBody'
import { LiveActivity } from './LiveActivity'

export function AskThread({ state, messages, pending, starters, projectName, onStarter, onRetry }) {
  if (state === 'loading') return <div className="pt-6"><SkeletonList rows={4} icon={false} /></div>

  if (state === 'error') {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Couldn’t load this conversation."
        action={<Button variant="outline" size="sm" className="border-destructive/60 text-destructive" onClick={onRetry}>Try again</Button>}
      />
    )
  }

  if (!messages.length && !pending) {
    return (
      <EmptyState
        icon={MessageSquare}
        title="Ask about anything you’ve connected. Answers come only from graded articles, with citations."
        action={
          <div className="mt-1 flex flex-wrap justify-center gap-1.5">
            {starters.map((s) => (
              <button
                key={s} type="button" onClick={() => onStarter?.(s)}
                className="rounded-full border border-border bg-card px-3 py-1 text-xs text-foreground hover:border-primary"
              >
                {s}
              </button>
            ))}
          </div>
        }
      />
    )
  }

  return (
    <div className="flex flex-col gap-6 py-6">
      {messages.map((m) => (
        <div key={m.id} className={m.role === 'user' ? 'flex flex-col items-end gap-2' : 'flex flex-col gap-2'}>
          {m.role === 'user' ? (
            <div className="max-w-[80%] rounded-xl bg-[var(--user)] px-3.5 py-2.5 whitespace-pre-wrap">{m.text}</div>
          ) : (
            <>
              {m.process && <LiveActivity stage={m.process} done />}
              <AnswerBody text={m.text} citations={m.trust?.citations} />
              {m.trust && (
                <TrustLine
                  grades={m.trust.grades} articleCount={m.trust.articleCount}
                  projectName={projectName} heads={m.trust.heads}
                  uncited={m.trust.citations?.length === 0}
                />
              )}
            </>
          )}
        </div>
      ))}

      {pending && (
        <div className="flex flex-col gap-2">
          <LiveActivity stage={pending.stage} />
          {pending.text && <AnswerBody text={pending.text} citations={[]} streaming />}
        </div>
      )}
    </div>
  )
}
