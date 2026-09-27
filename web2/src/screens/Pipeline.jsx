import { PageHeader } from '@/components/PageHeader'
import { Pipeline } from './connect/Pipeline'

export function PipelineScreen({ projectId, projectName, onNavigate }) {
  return <div className="flex min-h-0 flex-1 flex-col">
    <PageHeader title="Pipeline" subtitle={`Control how ${projectName} stays up to date. Sync, extract, and absorb on your terms.`} />
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <div className="mx-auto w-full max-w-(--page-width)"><Pipeline projectId={projectId} onNavigate={onNavigate} /></div>
    </div>
  </div>
}
