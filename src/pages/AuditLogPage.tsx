import { type FormEvent, useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, AppError } from "../lib/api";
import type { AuditItem, AuditResponse } from "../types";

interface AuditFilters { actorId: string; action: string; objectType: string; objectId: string; occurredFrom: string; occurredTo: string; limit: string }
const emptyFilters: AuditFilters = { actorId: "", action: "", objectType: "", objectId: "", occurredFrom: "", occurredTo: "", limit: "100" };

function queryFor(filters: AuditFilters) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
  return query.toString();
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function AuditLogPage() {
  const [result, setResult] = useState<AuditResponse | null>(null);
  const [filters, setFilters] = useState<AuditFilters>(emptyFilters);
  const [activeFilters, setActiveFilters] = useState<AuditFilters>(emptyFilters);
  const [detail, setDetail] = useState<AuditItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(next: AuditFilters = activeFilters) {
    setLoading(true); setError(null);
    try { setResult(await apiGet<AuditResponse>(`/audit-logs?${queryFor(next)}`)); setActiveFilters(next); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "操作记录加载失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(emptyFilters); }, []);

  function submit(event: FormEvent) { event.preventDefault(); setDetail(null); void load(filters); }
  function reset() { setFilters(emptyFilters); setDetail(null); void load(emptyFilters); }
  async function openDetail(id: number) {
    setDetailLoading(true); setError(null);
    try { setDetail(await apiGet<AuditItem>(`/audit-logs/${id}`)); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "操作记录详情加载失败"); }
    finally { setDetailLoading(false); }
  }

  if (loading && !result) return <LoadingState label="正在读取操作记录" />;
  if (error && !result) return <ErrorState message={error} onRetry={() => void load(activeFilters)} />;
  const facets = result?.facets ?? { actors: [], actions: [], objectTypes: [] };
  const hasFilters = Object.entries(activeFilters).some(([key, value]) => key !== "limit" && Boolean(value));

  return <div className="page-stack audit-page">
    <PageHeader eyebrow="AUDIT TRAIL" title="操作记录" description="按操作人、时间、动作和对象定位关键变化；记录只能由服务端追加。" actions={<span className="readonly-badge">不可编辑或删除</span>} />
    {error && <div className="inline-error" role="alert">{error}</div>}
    <form className="panel audit-filters" aria-label="操作记录筛选" onSubmit={submit}>
      <label className="field"><span>操作人</span><select aria-label="审计操作人" value={filters.actorId} onChange={(event) => setFilters({ ...filters, actorId: event.target.value })}><option value="">全部操作人</option>{facets.actors.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
      <label className="field"><span>动作</span><select aria-label="审计动作" value={filters.action} onChange={(event) => setFilters({ ...filters, action: event.target.value })}><option value="">全部动作</option>{facets.actions.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
      <label className="field"><span>对象类型</span><select aria-label="审计对象类型" value={filters.objectType} onChange={(event) => setFilters({ ...filters, objectType: event.target.value })}><option value="">全部对象</option>{facets.objectTypes.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
      <label className="field"><span>对象 ID（精确）</span><input aria-label="审计对象 ID" maxLength={200} value={filters.objectId} onChange={(event) => setFilters({ ...filters, objectId: event.target.value })} placeholder="输入完整对象 ID" /></label>
      <label className="field"><span>开始时间</span><input aria-label="审计开始时间" type="datetime-local" value={filters.occurredFrom} onChange={(event) => setFilters({ ...filters, occurredFrom: event.target.value })} /></label>
      <label className="field"><span>结束时间</span><input aria-label="审计结束时间" type="datetime-local" value={filters.occurredTo} onChange={(event) => setFilters({ ...filters, occurredTo: event.target.value })} /></label>
      <label className="field"><span>每次显示</span><select aria-label="审计显示上限" value={filters.limit} onChange={(event) => setFilters({ ...filters, limit: event.target.value })}><option value="50">50 条</option><option value="100">100 条</option><option value="200">200 条</option></select></label>
      <div className="audit-filter-actions"><button className="button" disabled={loading} type="submit">{loading ? "查询中…" : "查询记录"}</button><button className="button button--secondary" disabled={loading} type="button" onClick={reset}>重置筛选</button></div>
    </form>
    <section className="audit-summary"><strong>{result?.total ?? 0}</strong><span>条匹配记录</span><small>当前显示 {result?.items.length ?? 0} 条{hasFilters ? " · 已应用筛选" : " · 全部记录"}</small></section>
    {result?.items.length ? <section className="panel audit-table" aria-label="操作记录列表"><div className="audit-table__head"><span>时间</span><span>操作者</span><span>动作</span><span>对象</span><span>详情</span></div>{result.items.map((item) => <button className="audit-table__row" type="button" key={item.id} onClick={() => void openDetail(item.id)}><time>{formatTime(item.occurred_at)}</time><span>{item.actor_id}</span><code>{item.action}</code><span>{item.object_type} / {item.object_id.slice(0, 16)}</span><strong>{item.detail}</strong></button>)}</section> : <EmptyState title={hasFilters ? "当前筛选没有记录" : "还没有操作记录"} description={hasFilters ? "调整条件或重置筛选后重试。" : "关键操作发生后会显示在这里。"} action={hasFilters ? <button className="button button--secondary" type="button" onClick={reset}>重置筛选</button> : undefined} />}
    {detailLoading && <LoadingState label="正在读取记录详情" />}
    {detail && <aside className="panel audit-detail" role="dialog" aria-label="操作记录详情" aria-modal="false"><div className="panel__heading"><div><span className="eyebrow">IMMUTABLE RECORD #{detail.id}</span><h2>操作记录详情</h2></div><button className="button button--secondary" type="button" onClick={() => setDetail(null)}>关闭详情</button></div><dl><div><dt>发生时间</dt><dd>{formatTime(detail.occurred_at)}</dd></div><div><dt>操作者</dt><dd><code>{detail.actor_id}</code></dd></div><div><dt>动作</dt><dd><code>{detail.action}</code></dd></div><div><dt>对象类型</dt><dd>{detail.object_type}</dd></div><div><dt>对象 ID</dt><dd><code>{detail.object_id}</code></dd></div><div><dt>记录摘要</dt><dd>{detail.detail}</dd></div></dl><p>该记录由服务端追加，不提供编辑或删除接口。</p></aside>}
  </div>;
}
