import { MessageSquare, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'
import { SkeletonList } from '@/components/SkeletonList'
import { TrustLine } from '@/components/TrustLine'
import { AnswerBody } from './AnswerBody'
import { LiveActivity } from './LiveActivity'

export function AskThread({ state, messages, pending, starters, projectName, onStarter, onRetry, onGoFiles }) {
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
        className="pt-14"
        titleClassName="max-w-lg text-xl font-semibold"
        detailClassName="max-w-md text-[13px]"
        title="Make sense of your knowledge."
        detail="Ask across the files you have absorbed. Cairn reads relevant wiki articles and links its answer to the source evidence."
        action={
          <div className="mt-3 flex max-w-xl flex-col items-center gap-3">
          <div className="flex flex-wrap justify-center gap-2">
            {starters.map((s) => (
              <button
                key={s} type="button" onClick={() => onStarter?.(s)}
                className="rounded-lg border border-border bg-card px-3 py-2 text-left text-[13px] text-foreground hover:border-primary"
              >
                {s}
              </button>
            ))}
          </div>
          <Button variant="ghost" size="sm" onClick={onGoFiles}>Review files and absorption status</Button>
          </div>
        }
      />
    )
  }

  return (
    <div className="flex flex-col gap-7 py-6" aria-live="polite" aria-busy={!!pending}>
      {messages.map((m) => (
        <div key={m.id} className={m.role === 'user' ? 'flex flex-col items-end gap-2' : 'flex flex-col gap-2'}>
          {m.role === 'user' ? (
            <div className="max-w-[90%] rounded-xl bg-[var(--user)] px-4 py-3 text-[15px] whitespace-pre-wrap sm:max-w-[80%]">{m.text}</div>
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
