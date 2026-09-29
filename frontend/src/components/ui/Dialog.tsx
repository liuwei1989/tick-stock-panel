import { type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Modal } from '@/components/Modal'

/**
 * 共享对话框原语 — 封装现有 Modal(保留 ESC/焦点陷阱/遮罩关闭等无障碍能力),
 * 并统一「标题栏 + 右上关闭按钮」的视觉, 收敛各页手写弹窗的不一致。
 *
 * 用法:
 *   <Dialog open={open} onClose={close} title="新建策略" footer={<Button>保存</Button>}>
 *     ...内容...
 *   </Dialog>
 *
 * size 控制面板最大宽度: sm(最大 20rem) / md(28rem) / lg(36rem) / xl(48rem) / full(64rem)
 */
export type DialogSize = 'sm' | 'md' | 'lg' | 'xl' | 'full'

const sizeMap: Record<DialogSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
  full: 'max-w-4xl',
}

export interface DialogProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  size?: DialogSize
  /** 面板额外 className(覆盖默认尺寸/背景/圆角等) */
  panelClassName?: string
  /** 点击遮罩是否关闭 (默认 true) */
  closeOnBackdrop?: boolean
  /** 无可见标题时的无障碍名称 */
  ariaLabel?: string
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  panelClassName,
  closeOnBackdrop = true,
  ariaLabel,
}: DialogProps) {
  if (!open) return null
  return (
    <Modal
      onClose={onClose}
      ariaLabel={title ? undefined : ariaLabel}
      closeOnBackdrop={closeOnBackdrop}
      panelClassName={cn(
        'flex max-h-[90vh] flex-col bg-surface border border-border rounded-dialog shadow-xl',
        sizeMap[size],
        panelClassName,
      )}
    >
      {(title || description) && (
        <header className="flex items-start justify-between gap-4 px-5 py-3 border-b border-border">
          <div className="min-w-0">
            {title && <h2 className="text-[16px] leading-6 font-semibold tracking-tight text-foreground">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="-mr-1 -mt-0.5 shrink-0 rounded-btn p-1 text-muted transition-colors hover:bg-elevated hover:text-foreground"
          >
            <X size={16} />
          </button>
        </header>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-gutter-stable px-5 py-4">
        {children}
      </div>
      {footer && (
        <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-border">
          {footer}
        </footer>
      )}
    </Modal>
  )
}
