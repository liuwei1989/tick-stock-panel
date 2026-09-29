import { forwardRef, type InputHTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/cn'

/**
 * 共享输入框原语 — §6.0 设计语言。
 * - 圆角统一 rounded-input(4px)
 * - 聚焦态用 accent 描边/光环, 不引入新颜色
 * - 暗/亮主题自动随 CSS 变量切换
 */
export const inputVariants = cva(
  'w-full bg-surface text-foreground placeholder:text-muted border border-border rounded-input ' +
    'px-3 py-1.5 text-sm outline-none transition-colors duration-150 ease-smooth ' +
    'focus:border-accent focus:ring-1 focus:ring-accent/40 ' +
    'disabled:opacity-50 disabled:pointer-events-none',
  {
    variants: {
      size: {
        sm: 'h-7 px-2 text-xs',
        md: 'h-8',
        lg: 'h-9',
      },
    },
    defaultVariants: { size: 'md' },
  },
)

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'>,
    VariantProps<typeof inputVariants> {}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, size, ...props }, ref) => (
    <input ref={ref} className={cn(inputVariants({ size }), className)} {...props} />
  ),
)
Input.displayName = 'Input'
