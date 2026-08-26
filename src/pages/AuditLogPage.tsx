import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, AppError } from "../lib/api";
import type { AuditItem } from "../types";

export function AuditLogPage() {
  const [items, setItems] = useState<AuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    setLoading(true); setError(null);
    try { setItems((await apiGet<{ items: AuditItem[] }>("/audit-logs")).items); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "操作记录加载失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  if (loading) return <LoadingState label="正在读取操作记录" />;
  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  return <div className="page-stack"><PageHeader eyebrow="AUDIT TRAIL" title="操作记录" description="追踪产品、文件、解析、确认与权限拒绝等关键动作；记录只能由服务端写入。" />{items.length === 0 ? <EmptyState title="还没有操作记录" description="关键操作发生后会显示在这里。" /> : <section className="panel audit-table" aria-label="操作记录列表"><div className="audit-table__head"><span>时间</span><span>操作者</span><span>动作</span><span>对象</span><span>详情</span></div>{items.map((item) => <div className="audit-table__row" key={item.id}><time>{item.occurred_at.replace("T", " ").slice(0, 16)}</time><span>{item.actor_id}</span><code>{item.action}</code><span>{item.object_type} / {item.object_id.slice(0, 12)}</span><strong>{item.detail}</strong></div>)}</section>}</div>;
}
