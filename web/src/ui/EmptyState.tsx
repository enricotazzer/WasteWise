import type { ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

interface Props {
  icon?: IconName
  title: ReactNode
  /** What to do about it. An empty state that only says "nothing here" wastes the moment. */
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ icon, title, description, action }: Props) {
  return (
    <div className="empty">
      {icon && <Icon name={icon} size={26} className="empty-icon" />}
      <p className="empty-title">{title}</p>
      {description && <p className="empty-desc">{description}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  )
}
