import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, AppError } from "../lib/api";
import type { ReviewTask } from "../types";

const stageLabel = { product: "产品事实审核", compliance: "合规审核" } as const;
const statusLabel = { pending: "待开始", in_progress: "审核中", approved: "已通过", rejected: "已驳回", cancelled: "已撤回" } as const;
const channelLabel = { wechat: "公众号", xiaohongshu: "小红书" } as const;

function idempotencyKey(keys: Map<string, string>, action: string) {
  const existing = keys.get(action);
  if (existing) return existing;
  const value = crypto.randomUUID();
  keys.set(action, value);
  return value;
}

export function ReviewCenterPage() {
  const [items, setItems] = useState<ReviewTask[]>([]);
  const [active, setActive] = useState<ReviewTask | null>(null);
  const [stage, setStage] = useState("");
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const keys = useRef(new Map<string, string>());

  async function loadList(nextStage = stage, nextStatus = status) {
    setLoading(true); setError(null);
    try {
      const query = new URLSearchParams();
      if (nextStage) query.set("stage", nextStage);
      if (nextStatus) query.set("status", nextStatus);
      const result = await apiGet<{ items: ReviewTask[] }>(`/reviews${query.size ? `?${query}` : ""}`);
      setItems(result.items);
    } catch (cause) { setError(cause instanceof AppError ? cause.message : "审核任务读取失败"); }
    finally { setLoading(false); }
  }

  async function openTask(id: string) {
    setBusy(true); setError(null); setNotice("");
    try { setActive(await apiGet<ReviewTask>(`/reviews/${id}`)); setReason(""); }
    catch (cause) { setError(cause instanceof AppError ? cause.message : "审核详情读取失败"); }
    finally { setBusy(false); }
  }

  async function startReview() {
    if (!active) return;
    setBusy(true); setError(null);
    try { setActive(await apiJson<ReviewTask>(`/reviews/${active.id}/start`, "POST")); setNotice("审核已开始，提交版本已锁定"); await loadList(); }
    catch (cause) { setError(cause instanceof AppError ? cause.message : "开始审核失败"); }
    finally { setBusy(false); }
  }

  async function decide(decision: "approve" | "reject") {
    if (!active) return;
    if (decision === "reject" && !reason.trim()) { setError("驳回必须填写原因"); return; }
    const action = `${active.id}:${decision}`;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ReviewTask>(`/reviews/${active.id}/${decision}`, "POST", { reason: reason.trim() }, 30_000, { "Idempotency-Key": idempotencyKey(keys.current, action) });
      keys.current.delete(action);
      setActive(result); setReason("");
      setNotice(decision === "approve" ? (result.stage === "compliance" && result.status === "pending" ? "产品审核通过，已进入合规审核" : "合规审核通过，内容已批准") : "内容已驳回，创作者可按原因返回修改");
      await loadList();
    } catch (cause) { setError(cause instanceof AppError ? cause.message : "审核决定保存失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void loadList(); }, []);

  if (loading && !active) return <LoadingState label="正在读取审核任务" />;
  if (error && !active && items.length === 0) return <ErrorState message={error} onRetry={() => void loadList()} />;

  return <div className="page-stack">
    <PageHeader eyebrow="REVIEW CENTER" title="审核中心" description="产品事实审核通过后进入合规审核；审核人只对不可变提交版本作出决定，不直接修改创作者正文。" />
    {(error || notice) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}>{error ?? notice}</div>}
    {active ? <ReviewDetail task={active} busy={busy} reason={reason} onReason={setReason} onBack={() => { setActive(null); setError(null); setNotice(""); }} onStart={startReview} onApprove={() => decide("approve")} onReject={() => decide("reject")} /> : <>
      <section className="panel review-filters" aria-label="审核任务筛选">
        <label className="field"><span>审核阶段</span><select aria-label="审核阶段" value={stage} onChange={(event) => { const value = event.target.value; setStage(value); void loadList(value, status); }}><option value="">全部阶段</option><option value="product">产品事实审核</option><option value="compliance">合规审核</option></select></label>
        <label className="field"><span>任务状态</span><select aria-label="审核状态" value={status} onChange={(event) => { const value = event.target.value; setStatus(value); void loadList(stage, value); }}><option value="">全部状态</option><option value="pending">待开始</option><option value="in_progress">审核中</option><option value="approved">已通过</option><option value="rejected">已驳回</option><option value="cancelled">已撤回</option></select></label>
      </section>
      {items.length ? <section className="review-task-grid" aria-label="审核任务列表">{items.map((item) => <button type="button" className="review-task-card" key={item.id} onClick={() => void openTask(item.id)}><span className={`status-badge status-badge--${item.status}`}>{statusLabel[item.status]}</span><span className="eyebrow">{stageLabel[item.stage]}</span><strong>{item.contentTitle}</strong><p>{item.contentType.toUpperCase()}{item.branchChannel ? ` · ${channelLabel[item.branchChannel]}` : ""} · 提交版本 V{item.contentVersion}</p><small>{new Date(item.updatedAt).toLocaleString("zh-CN")}</small><i>查看不可变版本 →</i></button>)}</section> : <EmptyState title="当前筛选下没有审核任务" description="创作页完成关键确认并提交后，产品审核任务会显示在这里。" action={<Link className="button button--secondary" to="/create/article">返回内容创作</Link>} />}
    </>}
  </div>;
}

function ReviewDetail({ task, busy, reason, onReason, onBack, onStart, onApprove, onReject }: { task: ReviewTask; busy: boolean; reason: string; onReason: (value: string) => void; onBack: () => void; onStart: () => Promise<void>; onApprove: () => Promise<void>; onReject: () => Promise<void> }) {
  const actionable = task.status === "pending" || task.status === "in_progress";
  return <div className="page-stack">
    <button className="back-link" type="button" onClick={onBack}>← 返回审核任务</button>
    <section className="panel review-detail-head"><div><span className="eyebrow">{stageLabel[task.stage]}</span><h2>{task.contentTitle}</h2><p>{task.contentType.toUpperCase()}{task.branchChannel ? ` · ${channelLabel[task.branchChannel]}` : ""} · 不可变提交版本 V{task.contentVersion}</p></div><span className={`status-badge status-badge--${task.status}`}>{statusLabel[task.status]}</span></section>
    <div className="review-detail-grid">
      <section className="panel"><div className="panel__heading"><div><span className="eyebrow">SOURCE & CONTENT</span><h2>来源与提交内容</h2></div></div><pre className="review-snapshot">{JSON.stringify(task.snapshot ?? {}, null, 2)}</pre></section>
      <aside className="panel review-decision"><span className="eyebrow">DECISION</span><h2>审核决定</h2><p>审核过程不修改原文。需要修改时填写具体原因并驳回，由创作者生成新修订后重新提交。</p>{task.status === "pending" && <button className="button button--secondary" type="button" disabled={busy} onClick={() => void onStart()}>开始审核</button>}{actionable && <><label className="field"><span>审核说明 / 驳回原因</span><textarea aria-label="审核说明或驳回原因" rows={5} maxLength={1000} value={reason} onChange={(event) => onReason(event.target.value)} placeholder="通过可选填；驳回必须说明问题位置和修改要求" /></label><div className="review-decision__actions"><button className="button" type="button" disabled={busy} onClick={() => void onApprove()}>审核通过</button><button className="button button--danger" type="button" disabled={busy} onClick={() => void onReject()}>驳回修改</button></div></>}{!actionable && <p className="review-final-state">该任务已处理，决定与记录不可修改。</p>}</aside>
    </div>
    <section className="panel"><div className="panel__heading"><div><span className="eyebrow">REVIEW TRAIL</span><h2>审核记录</h2></div></div>{task.records?.length ? <div className="review-records">{task.records.map((record) => <div key={record.id}><time>{new Date(record.createdAt).toLocaleString("zh-CN")}</time><strong>{record.actorId}</strong><span>{record.decision}</span><p>{record.reason || "—"}</p></div>)}</div> : <p className="studio-hint">任务尚未开始。</p>}</section>
  </div>;
}
