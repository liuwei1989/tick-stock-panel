import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/cn'

/**
 * 共享按钮原语 — §6.0 设计语言。
 * - 圆角统一 rounded-btn(6px)
 * - 配色绑定设计 token: primary=accent 电光蓝, danger=红跌色, 其余走 surface/elevated/border
 * - 暗/亮主题自动随 CSS 变量切换
 *
 * 用法: <Button variant="primary" size="sm" onClick={...}>保存</Button>
 *       <Button variant="ghost" asChild? > … </Button>  (图标按钮用 size="icon")
 */
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 font-medium rounded-btn select-none whitespace-nowrap ' +
    'transition-colors duration-150 ease-smooth ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ' +
    'disabled:opacity-50 disabled:pointer-events-none',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-white hover:bg-accent/90',
        secondary: 'bg-elevated text-foreground border border-border hover:bg-elevated/70',
        outline: 'border border-border text-foreground hover:bg-elevated',
        ghost: 'text-secondary hover:bg-elevated hover:text-foreground',
        danger: 'bg-danger text-white hover:bg-danger/90',
      },
      size: {
        xs: 'h-6 px-2 text-[11px]',
        sm: 'h-7 px-2.5 text-xs',
        md: 'h-8 px-3 text-sm',
        lg: 'h-9 px-4 text-sm',
        icon: 'h-8 w-8 p-0',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = 'button', ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  ),
)
Button.displayName = 'Button'
