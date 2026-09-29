import { forwardRef, type HTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/cn'

/**
 * 共享卡片原语 — §6.0 设计语言。
 * - 圆角统一 rounded-card(8px), 边框统一 border-border
 * - tone: surface(默认) / elevated(略亮层级)
 * - p: 内边距档位, 默认 md(p-4)
 */
export const cardVariants = cva('border border-border rounded-card', {
  variants: {
    tone: {
      surface: 'bg-surface',
      elevated: 'bg-elevated',
    },
    p: {
      none: '',
      sm: 'p-3',
      md: 'p-4',
      lg: 'p-5',
    },
  },
  defaultVariants: { tone: 'surface', p: 'md' },
})

export interface CardProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof cardVariants> {}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, tone, p, ...props }, ref) => (
    <div ref={ref} className={cn(cardVariants({ tone, p }), className)} {...props} />
  ),
)
Card.displayName = 'Card'
