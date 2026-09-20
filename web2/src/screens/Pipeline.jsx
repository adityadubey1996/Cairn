import { Pipeline } from './connect/Pipeline'

export function PipelineScreen({ projectId, projectName, onNavigate }) {
  return <div className="flex min-h-0 flex-1 flex-col">
    <header className="border-b border-border px-6 py-5">
      <h1 className="text-xl font-semibold">Pipeline</h1>
      <p className="mt-1 text-[13px] text-muted-foreground">Control how {projectName} stays up to date. Sync, extract, and absorb on your terms.</p>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5"><Pipeline projectId={projectId} onNavigate={onNavigate} /></div>
  </div>
}
