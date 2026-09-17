import { Cloud, FileText, Globe, Mail, MessageSquare } from 'lucide-react'
import { cn } from '@/lib/utils'

// lucide 1.32 carries no brand marks, so the branded connectors get simple
// geometric placeholders in the same 24-grid, 2px stroke style as the rest.
// They read as "which service" without pretending to be the real logo.
const MARKS = {
  github: <path d="M15 22v-4a4 4 0 0 0-1-3c3 0 6-2 6-5.5a5 5 0 0 0-1.4-3.5 4.6 4.6 0 0 0-.1-3.5s-1.1-.3-3.5 1.3a12 12 0 0 0-6 0C6.6 2.2 5.5 2.5 5.5 2.5a4.6 4.6 0 0 0-.1 3.5A5 5 0 0 0 4 9.5C4 13 7 15 10 15a4 4 0 0 0-1 3v4M9 18c-4.5 2-5-2-7-2" />,
  gdrive: <path d="M8.5 3h7l6.5 11h-7zM8.5 3 2 14l3.5 6 6.5-11zM5.5 20h13l3.5-6H9z" />,
  jira: <path d="M12 2 4 10l8 8 8-8z" />,
  teams: <path d="M3 6h12v12H3zM6 10h6M9 10v5M17 8h4v8h-4" />,
  whatsapp: <path d="M21 12a9 9 0 0 1-13.3 7.9L3 21l1.2-4.6A9 9 0 1 1 21 12zM9 9.5c0 3 2.5 5.5 5.5 5.5" />,
}

const LUCIDE = {
  gmail: Mail,
  outlook: Mail,
  onedrive: Cloud,
  gchat: MessageSquare,
  links: Globe,
  file: FileText,
}

export function ConnectorIcon({ kind, size = 16, className }) {
  const mark = MARKS[kind]
  if (mark) {
    return (
      <svg
        viewBox="0 0 24 24" width={size} height={size} aria-hidden
        fill="none" stroke="currentColor" strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round"
        className={cn('shrink-0', className)}
      >
        {mark}
      </svg>
    )
  }
  const Icon = LUCIDE[kind] ?? FileText
  return <Icon size={size} className={cn('shrink-0', className)} aria-hidden />
}
