import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

type Variant = 'primary' | 'secondary' | 'ghost' | 'icon'

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  variant?: Variant
  /** `lg` is the thumb-sized call to action — the "Scansiona la spesa" case. */
  size?: 'md' | 'lg'
  tone?: 'danger'
  icon?: IconName
  className?: string
  children?: ReactNode
}

// Emitted class names stay the ones already in index.css (`primary`, `iconbtn`) rather than
// a fresh `btn-*` family, so the handful of places that still write a bare <button> keep
// rendering identically instead of drifting into a second button look.
const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'primary',
  secondary: 'btn-secondary',
  ghost: 'btn-ghost',
  icon: 'iconbtn',
}

/**
 * `variant="icon"` requires an `aria-label` from the caller — an icon button with no
 * accessible name is a button with no name at all, and every one of them in this app is a
 * destructive-or-irreversible inventory action.
 */
export function Button({
  variant = 'secondary',
  size = 'md',
  tone,
  icon,
  className,
  children,
  type = 'button',
  ...rest
}: Props) {
  const classes = [VARIANT_CLASS[variant], size === 'lg' && 'big', tone, className]
    .filter(Boolean)
    .join(' ')

  return (
    <button type={type} className={classes} {...rest}>
      {icon && <Icon name={icon} size={variant === 'icon' ? 19 : 17} />}
      {children}
    </button>
  )
}
