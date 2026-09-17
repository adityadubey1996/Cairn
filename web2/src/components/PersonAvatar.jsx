import { cn } from '@/lib/utils'

// Initials only — this product has no photo upload.
const SIZES = { sm: 'size-7 text-[11px]', md: 'size-8.5 text-xs', lg: 'size-10 text-sm' }

export function PersonAvatar({ name, initials, size = 'sm', className }) {
  const text = initials
    ?? (name ?? '?').split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()
  return (
    <span
      aria-hidden
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full border border-[#262b36] bg-accent',
        'font-semibold text-foreground',
        SIZES[size], className,
      )}
    >
      {text}
    </span>
  )
}
