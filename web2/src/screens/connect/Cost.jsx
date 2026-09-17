import { Coins } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/EmptyState'

// Deliberately empty. Token accounting has not landed, so there is nothing
// real to plot — and a fake chart here would be the one screen in the product
// that lies about evidence. Section 3d of the spec says so explicitly.
export function Cost({ onGoHealth }) {
  return (
    <div className="flex min-h-[380px] items-center justify-center">
      <EmptyState
        icon={Coins}
        title="Cost tracking is on the way"
        detail="Once that work lands, this view will show real token spend per project and per run."
        action={
          <Button variant="outline" size="sm" className="mt-1" onClick={onGoHealth}>
            Back to Health
          </Button>
        }
      />
    </div>
  )
}
