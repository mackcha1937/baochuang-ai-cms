import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { useAppContext } from "../context/AppContext";

export function AuditLogPage() {
  const { data, error, loading, reload } = useAppContext();
  if (loading) return <LoadingState label="正在读取操作记录" />;
  if (error || !data) return <ErrorState message={error ?? "操作记录为空"} onRetry={() => void reload()} />;
  return <div className="page-stack"><PageHeader eyebrow="AUDIT TRAIL" title="操作记录" description="查看不可在前端修改的启动审计数据，为后续业务操作追踪预留统一入口。" />{data.recentAudit.length === 0 ? <EmptyState title="还没有操作记录" description="后续关键操作发生后会显示在这里。" /> : <section className="panel audit-table" aria-label="操作记录列表"><div className="audit-table__head"><span>时间</span><span>操作者</span><span>动作</span><span>对象</span><span>详情</span></div>{data.recentAudit.map((item) => <div className="audit-table__row" key={item.id}><time>{item.occurred_at.replace("T", " ").slice(0, 16)}</time><span>超级演示用户</span><code>{item.action}</code><span>{item.object_type} / {item.object_id}</span><strong>{item.detail}</strong></div>)}</section>}</div>;
}
