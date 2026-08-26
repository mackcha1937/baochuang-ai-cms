import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, apiRequest, AppError } from "../lib/api";
import type { ContentBranch, ContentItem, ContentType, GenerationJob, ProductSummary } from "../types";

const labels = {
  image: { eyebrow: "AI IMAGE COPY", title: "AI图片创作", description: "先生成并确认保险营销文案，再进入后续视觉生成阶段。" },
  article: { eyebrow: "AI ARTICLE", title: "AI文章创作", description: "公众号与小红书框架独立确认，单渠道失败不影响另一渠道。" },
  training: { eyebrow: "AI TRAINING", title: "AI动画培训", description: "先确认课程框架和讲稿，为后续语音、字幕与答题准备内容。" },
};

const channelLabel = { wechat: "公众号", xiaohongshu: "小红书" };
const AI_TASK_TIMEOUT_MS = 165_000;

function pretty(value: unknown) {
  if (!value || typeof value !== "object") return "";
  return JSON.stringify(value, null, 2);
}

export function ContentStudioPage({ type }: { type: ContentType }) {
  const meta = labels[type];
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [items, setItems] = useState<ContentItem[]>([]);
  const [active, setActive] = useState<ContentItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [productId, setProductId] = useState("darwin-12");
  const [manualSource, setManualSource] = useState("");
  const [channels, setChannels] = useState<string[]>(["wechat"]);

  async function loadList() {
    setLoading(true); setError(null);
    try {
      const [productResult, contentResult] = await Promise.all([
        apiGet<{ items: ProductSummary[] }>("/products"),
        apiGet<{ items: ContentItem[] }>(`/contents?type=${type}`),
      ]);
      setProducts(productResult.items.filter((product) => product.status === "已确认"));
      setItems(contentResult.items);
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "创作项目加载失败"); }
    finally { setLoading(false); }
  }

  async function openItem(id: string) {
    setBusy(true); setError(null);
    try { setActive(await apiGet<ContentItem>(`/contents/${id}`)); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "项目加载失败"); }
    finally { setBusy(false); }
  }

  async function createItem() {
    if (!title.trim()) { setError("请输入项目标题"); return; }
    if (!productId && !manualSource.trim()) { setError("请选择已确认产品或填写人工素材"); return; }
    setBusy(true); setError(null);
    try {
      const settings: Record<string, unknown> = type === "article" ? { channels } : type === "training" ? { audience: "保险代理人", duration: 3 } : { audience: "家庭用户", purpose: "产品科普", imageType: "长图" };
      const created = await apiJson<ContentItem>("/contents", "POST", { type, title: title.trim(), productId: productId || null, manualSource, settings });
      setActive(created); setShowCreate(false); setNotice("创作草稿已保存"); await loadList();
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "创建失败"); }
    finally { setBusy(false); }
  }

  async function refreshActive() {
    if (active) setActive(await apiGet<ContentItem>(`/contents/${active.id}`));
  }

  async function runTask(taskType: string, branchId?: string, instruction = "") {
    if (!active) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const key = crypto.randomUUID();
      const job = await apiRequest<GenerationJob>(`/contents/${active.id}/jobs`, {
        method: "POST", headers: { "Idempotency-Key": key },
        body: JSON.stringify({ taskType, branchId: branchId ?? null, instruction }),
      });
      const completed = await apiRequest<GenerationJob>(`/jobs/${job.id}/run`, { method: "POST" }, AI_TASK_TIMEOUT_MS);
      await refreshActive();
      if (completed.status === "failed") setError(completed.error);
      else setNotice("AI 任务已完成，结果已保存为草稿");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "生成任务失败"); }
    finally { setBusy(false); }
  }

  async function retryJob(job: GenerationJob) {
    setBusy(true); setError(null);
    try {
      const result = await apiJson<GenerationJob>(`/jobs/${job.id}/retry`, "POST", undefined, AI_TASK_TIMEOUT_MS);
      await refreshActive();
      if (result.status === "failed") setError(result.error); else setNotice("任务重试成功");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "重试失败"); }
    finally { setBusy(false); }
  }

  async function cancelJob(job: GenerationJob) {
    setBusy(true); setError(null);
    try { await apiJson(`/jobs/${job.id}/cancel`, "POST"); await refreshActive(); setNotice("任务已取消，输入快照仍保留"); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "取消失败"); }
    finally { setBusy(false); }
  }

  async function saveManual(taskType: string, output: Record<string, unknown>, branchId?: string) {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/manual-output`, "PUT", { taskType, branchId: branchId ?? null, revision: active.revision, output });
      setActive(result); setNotice("人工内容已保存，可继续确认");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "人工内容保存失败"); }
    finally { setBusy(false); }
  }

  async function confirm(node: string, branchId?: string) {
    if (!active || !window.confirm("确认当前内容已经人工核对并锁定吗？")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/confirm`, "POST", { node, branchId: branchId ?? null, revision: active.revision });
      setActive(result); setNotice("当前节点已确认");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "确认失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { setActive(null); void loadList(); }, [type]);

  if (loading) return <LoadingState label={`正在读取${meta.title}项目`} />;
  if (error && !active && !showCreate) return <ErrorState message={error} onRetry={() => void loadList()} />;

  return <div className="page-stack">
    <PageHeader eyebrow={meta.eyebrow} title={meta.title} description={meta.description} actions={<button className="button" type="button" onClick={() => { setTitle(""); setManualSource(""); setError(null); setShowCreate(true); }}>＋ 新建创作</button>} />
    {(error || notice) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}>{error ?? notice}</div>}
    {active ? <Studio type={type} item={active} busy={busy} onBack={() => { setActive(null); setError(null); void loadList(); }} onRun={runTask} onRetry={retryJob} onCancel={cancelJob} onConfirm={confirm} onSaveManual={saveManual} /> : items.length ? <section className="content-project-grid">{items.map((item) => <button type="button" className="content-project-card" key={item.id} onClick={() => void openItem(item.id)}><span className="status-badge">{item.status}</span><span className="eyebrow">{item.type.toUpperCase()}</span><strong>{item.title}</strong><small>修订 {item.revision} · {new Date(item.updatedAt).toLocaleString("zh-CN")}</small><i>继续创作 →</i></button>)}</section> : <EmptyState title={`还没有${meta.title}项目`} description="新建草稿后，生成失败或刷新页面都不会丢失已经完成的输入。" action={<button className="button" type="button" onClick={() => setShowCreate(true)}>新建第一个项目</button>} />}
    {showCreate && <div className="modal-backdrop"><section className="modal-card studio-create" role="dialog" aria-modal="true" aria-labelledby="content-create-title"><span className="eyebrow">NEW AI CONTENT</span><h2 id="content-create-title">新建{meta.title}草稿</h2><p>选择已确认产品，或不选择产品并填写人工素材。AI Key 只由后端读取。</p><label className="field"><span>项目标题 *</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="field"><span>已确认产品</span><select value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">不使用产品档案</option>{products.map((product) => <option value={product.id} key={product.id}>{product.name}</option>)}</select></label><label className="field"><span>人工补充素材</span><textarea rows={4} value={manualSource} onChange={(event) => setManualSource(event.target.value)} placeholder="可补充受众、场景、限制或自有素材" /></label>{type === "article" && <fieldset className="channel-picker"><legend>发布渠道 *</legend>{(["wechat", "xiaohongshu"] as const).map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={(event) => setChannels((current) => event.target.checked ? [...current, channel] : current.filter((item) => item !== channel))} />{channelLabel[channel]}</label>)}</fieldset>}<div className="modal-actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => setShowCreate(false)}>取消</button><button className="button" type="button" disabled={busy || (type === "article" && !channels.length)} onClick={() => void createItem()}>创建草稿</button></div></section></div>}
  </div>;
}

function Studio({ type, item, busy, onBack, onRun, onRetry, onCancel, onConfirm, onSaveManual }: { type: ContentType; item: ContentItem; busy: boolean; onBack: () => void; onRun: (task: string, branchId?: string, instruction?: string) => Promise<void>; onRetry: (job: GenerationJob) => Promise<void>; onCancel: (job: GenerationJob) => Promise<void>; onConfirm: (node: string, branchId?: string) => Promise<void>; onSaveManual: (task: string, output: Record<string, unknown>, branchId?: string) => Promise<void> }) {
  return <div className="page-stack"><button className="back-link studio-back" type="button" onClick={onBack}>← 返回项目列表</button><section className="panel studio-summary"><div><span className="eyebrow">CONTENT DRAFT</span><h2>{item.title}</h2><p>修订 {item.revision} · {item.productId ? "使用已确认产品" : "使用人工素材"}</p></div><span className="status-badge">{item.status}</span></section>
    {type === "image" && <ImageStudio item={item} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} />}
    {type === "article" && <div className="branch-grid">{item.branches.map((branch) => <ArticleBranch key={branch.id} item={item} branch={branch} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} />)}</div>}
    {type === "training" && <TrainingStudio item={item} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} />}
    <JobPanel item={item} jobs={item.jobs} busy={busy} onRetry={onRetry} onCancel={onCancel} />
  </div>;
}

function ImageStudio({ item, busy, onRun, onConfirm, onSaveManual }: any) {
  const copy = item.payload.copy;
  return <section className="panel studio-node"><div className="panel__heading"><div><span className="eyebrow">COPY FIRST</span><h2>图片文案确认</h2></div><span className="status-badge">{item.payload.copyStatus === "confirmed" ? "已确认" : copy ? "待确认" : "未生成"}</span></div>{copy ? <pre className="structured-output">{pretty(copy)}</pre> : <p className="studio-hint">生成标题、卖点、区块结构和必要免责声明；本阶段不会产生图片费用。</p>}<ManualJsonEditor task="image_copy" value={copy} disabled={busy} onSave={(output) => onSaveManual("image_copy", output)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => void onRun("image_copy", undefined, copy ? "在保留事实的前提下优化表达" : "")}>{copy ? "重新生成文案" : "生成图片文案"}</button>{copy && item.payload.copyStatus !== "confirmed" && <button className="button" type="button" disabled={busy} onClick={() => void onConfirm("copy")}>确认文案</button>}<button className="button" type="button" disabled title="Stage 04 接入千问视觉生成">生成图片 · 后续阶段</button></div></section>;
}

function ArticleBranch({ item, branch, busy, onRun, onConfirm, onSaveManual }: { item: ContentItem; branch: ContentBranch; busy: boolean; onRun: any; onConfirm: any; onSaveManual: any }) {
  return <section className="panel studio-node"><div className="panel__heading"><div><span className="eyebrow">{branch.channel.toUpperCase()}</span><h2>{channelLabel[branch.channel]}分支</h2></div><span className="status-badge">框架 {branch.outlineStatus}</span></div>{branch.outlineStatus === "empty" ? <p className="studio-hint">该渠道拥有独立框架、失败和重试状态。</p> : <pre className="structured-output">{pretty(branch.outline)}</pre>}<ManualJsonEditor task="article_outline" value={branch.outline} disabled={busy || branch.outlineStatus === "confirmed"} onSave={(output) => onSaveManual("article_outline", output, branch.id)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || branch.outlineStatus === "confirmed"} onClick={() => void onRun("article_outline", branch.id)}>{branch.outlineStatus === "empty" ? "生成框架" : "重新生成框架"}</button>{branch.outlineStatus === "draft" && <button className="button" type="button" disabled={busy} onClick={() => void onConfirm("outline", branch.id)}>确认框架</button>}</div>{branch.outlineStatus === "confirmed" && <div className="article-body"><h3>完整文章</h3>{branch.bodyStatus === "empty" ? <p className="studio-hint">框架已锁定，可以生成全文。</p> : <pre className="structured-output">{pretty(branch.body)}</pre>}<ManualJsonEditor task="article_generate" value={branch.body} disabled={busy} onSave={(output) => onSaveManual("article_generate", output, branch.id)} /><button className="button" type="button" disabled={busy} onClick={() => void onRun("article_generate", branch.id)}>{branch.bodyStatus === "empty" ? "生成全文" : "重新生成全文"}</button></div>}</section>;
}

function TrainingStudio({ item, busy, onRun, onConfirm, onSaveManual }: any) {
  const outline = item.payload.outline, script = item.payload.script;
  const scriptStatus = item.payload.scriptStatus ?? (item.payload.outlineStatus === "confirmed" ? "empty" : "locked");
  return <div className="training-nodes"><section className="panel studio-node"><div className="panel__heading"><h2>01 课程框架</h2><span className="status-badge">{item.payload.outlineStatus ?? "empty"}</span></div>{outline ? <pre className="structured-output">{pretty(outline)}</pre> : <p className="studio-hint">按培训对象、目的和 3 分钟时长生成章节。</p>}<ManualJsonEditor task="training_outline" value={outline} disabled={busy || item.payload.outlineStatus === "confirmed"} onSave={(output) => onSaveManual("training_outline", output)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || item.payload.outlineStatus === "confirmed"} onClick={() => void onRun("training_outline")}>生成课程框架</button>{outline && item.payload.outlineStatus !== "confirmed" && <button className="button" type="button" onClick={() => void onConfirm("outline")}>确认框架</button>}</div></section><section className="panel studio-node"><div className="panel__heading"><h2>02 培训讲稿</h2><span className="status-badge">{scriptStatus}</span></div>{script ? <pre className="structured-output">{pretty(script)}</pre> : <p className="studio-hint">确认框架后生成逐章讲稿、知识卡和 3 道测试题。</p>}<ManualJsonEditor task="training_script" value={script} disabled={busy || item.payload.outlineStatus !== "confirmed" || item.payload.scriptStatus === "confirmed"} onSave={(output) => onSaveManual("training_script", output)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || item.payload.outlineStatus !== "confirmed" || item.payload.scriptStatus === "confirmed"} onClick={() => void onRun("training_script")}>生成培训讲稿</button>{script && item.payload.scriptStatus !== "confirmed" && <button className="button" type="button" onClick={() => void onConfirm("script")}>确认讲稿</button>}</div></section></div>;
}

function isJobLocked(item: ContentItem, job: GenerationJob) {
  if (job.taskType === "article_outline") return item.branches.find((branch) => branch.id === job.branchId)?.outlineStatus === "confirmed";
  if (job.taskType === "training_outline") return item.payload.outlineStatus === "confirmed";
  if (job.taskType === "training_script") return item.payload.scriptStatus === "confirmed";
  return false;
}

function JobPanel({ item, jobs, busy, onRetry, onCancel }: { item: ContentItem; jobs: GenerationJob[]; busy: boolean; onRetry: (job: GenerationJob) => Promise<void>; onCancel: (job: GenerationJob) => Promise<void> }) {
  return <section className="panel"><div className="panel__heading"><div><span className="eyebrow">GENERATION JOBS</span><h2>生成任务记录</h2></div></div>{jobs.length === 0 ? <p className="studio-hint">尚未创建任务。</p> : <div className="job-list">{jobs.map((job) => { const locked = isJobLocked(item, job); return <div key={job.id}><span className={`status-badge status-badge--${job.status}`}>{job.status}</span><strong>{job.taskType}</strong><small>attempt {job.attempt} · {job.provider}{job.model ? ` / ${job.model}` : ""}</small>{job.error && <p>{job.error}</p>}{locked && ["failed", "cancelled"].includes(job.status) && <small>节点已确认，历史任务已归档</small>}{!locked && ["failed", "cancelled"].includes(job.status) && <button type="button" disabled={busy} onClick={() => void onRetry(job)}>重试</button>}{["queued", "running"].includes(job.status) && <button type="button" disabled={busy} onClick={() => void onCancel(job)}>取消</button>}</div>; })}</div>}</section>;
}

const manualTemplates: Record<string, Record<string, unknown>> = {
  image_copy: { title: "", sellingPoints: [], blocks: [], disclaimer: "" },
  article_outline: { title: "", sections: [] },
  article_generate: { title: "", paragraphs: [], disclaimer: "" },
  training_outline: { title: "", chapters: [] },
  training_script: { chapters: [], questions: [{}, {}, {}] },
};

function ManualJsonEditor({ task, value, disabled, onSave }: { task: string; value: Record<string, unknown> | undefined; disabled: boolean; onSave: (output: Record<string, unknown>) => Promise<void> }) {
  const [text, setText] = useState(pretty(value && Object.keys(value).length ? value : manualTemplates[task]));
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => {
    setText(pretty(value && Object.keys(value).length ? value : manualTemplates[task]));
    setLocalError(null);
  }, [task, value]);
  return <details className="manual-json"><summary>人工填写 / 编辑结构化内容</summary><textarea aria-label={`${task} 人工内容`} rows={8} value={text} disabled={disabled} onChange={(event) => setText(event.target.value)} /><div>{localError && <span role="alert">{localError}</span>}<button className="button button--secondary" type="button" disabled={disabled} onClick={() => { try { const output = JSON.parse(text) as Record<string, unknown>; setLocalError(null); void onSave(output); } catch { setLocalError("JSON 格式无效，请检查括号和引号"); } }}>保存人工内容</button></div></details>;
}
