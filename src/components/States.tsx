import type { ReactNode } from "react";

export function LoadingState({ label = "正在加载" }: { label?: string }) {
  return (
    <div className="state-card" role="status" aria-live="polite">
      <span className="loader" aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <p>正在从本地服务读取数据，请稍候。</p>
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state-card state-card--error" role="alert">
      <span className="state-symbol" aria-hidden="true">!</span>
      <div>
        <strong>页面暂时无法加载</strong>
        <p>{message}</p>
        <button className="button button--secondary" type="button" onClick={onRetry}>重新加载</button>
      </div>
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-orbit" aria-hidden="true"><span /></div>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}
