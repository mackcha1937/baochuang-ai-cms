import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, apiRequest, AppError, uploadVisualFile } from "../lib/api";
import type { ContentBranch, ContentItem, ContentType, GenerationJob, ProductSummary, VisualDraft, VisualLayout } from "../types";

const labels = {
  image: { eyebrow: "AI IMAGE STUDIO", title: "AI图片创作", description: "先确认保险营销文案，再由千问生成无关键文字视觉素材并进行确定性排版。" },
  article: { eyebrow: "AI ARTICLE", title: "AI文章创作", description: "公众号与小红书框架独立确认，单渠道失败不影响另一渠道。" },
  training: { eyebrow: "AI TRAINING", title: "AI动画培训", description: "先确认课程框架和讲稿，为后续语音、字幕与答题准备内容。" },
};

const channelLabel = { wechat: "公众号", xiaohongshu: "小红书" };
const AI_TASK_TIMEOUT_MS = 165_000;
const VISUAL_POLL_TIMEOUT_MS = 210_000;

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

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

  async function waitForVisualJob(jobId: string) {
    const deadline = Date.now() + VISUAL_POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const current = await apiGet<GenerationJob>(`/jobs/${jobId}`, 15_000);
      if (["succeeded", "failed", "cancelled"].includes(current.status)) return current;
      setNotice("千问正在生成视觉素材，可在任务记录中取消；已确认文案不会丢失");
      await delay(2_000);
    }
    throw new AppError("图片任务仍在服务端执行，请稍后刷新查看或取消", null, "VISUAL_POLL_TIMEOUT");
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
      if (taskType === "image_visual") {
        setBusy(false);
        const visualResult = ["succeeded", "failed", "cancelled"].includes(completed.status) ? completed : await waitForVisualJob(job.id);
        await refreshActive();
        if (visualResult.status === "failed") setError(visualResult.error);
        else if (visualResult.status === "cancelled") setNotice("图片任务已取消，已确认文案和输入快照仍保留");
        else setNotice("视觉素材已保存，关键文字已由程序排入模板");
        return;
      }
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
      if (job.taskType === "image_visual") {
        setBusy(false);
        const visualResult = ["succeeded", "failed", "cancelled"].includes(result.status) ? result : await waitForVisualJob(job.id);
        await refreshActive();
        if (visualResult.status === "failed") setError(visualResult.error);
        else if (visualResult.status === "cancelled") setNotice("图片任务已取消，输入仍保留");
        else setNotice("图片任务重试成功，视觉素材已安全保存");
        return;
      }
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

  async function unlockCopy() {
    if (!active || !window.confirm("解锁后可修改文案，既有图片会标记为需重新排版。确定继续吗？")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/unlock-copy`, "POST", { revision: active.revision });
      setActive(result); setNotice("图片文案已解锁，既有图片已标记为需重新排版");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "解锁失败"); }
    finally { setBusy(false); }
  }

  async function saveVisualLayout(layout: Pick<VisualLayout, "template" | "palette" | "blockOrder" | "hiddenBlockIds">) {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/visual-layout`, "PATCH", { revision: active.revision, ...layout });
      setActive(result); setNotice("图片模板、配色和区块结构已保存");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "图片布局保存失败"); }
    finally { setBusy(false); }
  }

  async function uploadVisual(file: File) {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const result = await uploadVisualFile(active.id, active.revision, file) as ContentItem;
      setActive(result); setNotice("替换图已校验并保存，关键文字由程序继续排版");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "替换图上传失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { setActive(null); void loadList(); }, [type]);

  if (loading) return <LoadingState label={`正在读取${meta.title}项目`} />;
  if (error && !active && !showCreate) return <ErrorState message={error} onRetry={() => void loadList()} />;

  return <div className="page-stack">
    <PageHeader eyebrow={meta.eyebrow} title={meta.title} description={meta.description} actions={<button className="button" type="button" onClick={() => { setTitle(""); setManualSource(""); setError(null); setShowCreate(true); }}>＋ 新建创作</button>} />
    {(error || notice) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}>{error ?? notice}</div>}
    {active ? <Studio type={type} item={active} busy={busy} onBack={() => { setActive(null); setError(null); void loadList(); }} onRun={runTask} onRetry={retryJob} onCancel={cancelJob} onConfirm={confirm} onSaveManual={saveManual} onUnlockCopy={unlockCopy} onSaveVisualLayout={saveVisualLayout} onUploadVisual={uploadVisual} /> : items.length ? <section className="content-project-grid">{items.map((item) => <button type="button" className="content-project-card" key={item.id} onClick={() => void openItem(item.id)}><span className="status-badge">{item.status}</span><span className="eyebrow">{item.type.toUpperCase()}</span><strong>{item.title}</strong><small>修订 {item.revision} · {new Date(item.updatedAt).toLocaleString("zh-CN")}</small><i>继续创作 →</i></button>)}</section> : <EmptyState title={`还没有${meta.title}项目`} description="新建草稿后，生成失败或刷新页面都不会丢失已经完成的输入。" action={<button className="button" type="button" onClick={() => setShowCreate(true)}>新建第一个项目</button>} />}
    {showCreate && <div className="modal-backdrop"><section className="modal-card studio-create" role="dialog" aria-modal="true" aria-labelledby="content-create-title"><span className="eyebrow">NEW AI CONTENT</span><h2 id="content-create-title">新建{meta.title}草稿</h2><p>选择已确认产品，或不选择产品并填写人工素材。AI Key 只由后端读取。</p><label className="field"><span>项目标题 *</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="field"><span>已确认产品</span><select value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">不使用产品档案</option>{products.map((product) => <option value={product.id} key={product.id}>{product.name}</option>)}</select></label><label className="field"><span>人工补充素材</span><textarea rows={4} value={manualSource} onChange={(event) => setManualSource(event.target.value)} placeholder="可补充受众、场景、限制或自有素材" /></label>{type === "article" && <fieldset className="channel-picker"><legend>发布渠道 *</legend>{(["wechat", "xiaohongshu"] as const).map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={(event) => setChannels((current) => event.target.checked ? [...current, channel] : current.filter((item) => item !== channel))} />{channelLabel[channel]}</label>)}</fieldset>}<div className="modal-actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => setShowCreate(false)}>取消</button><button className="button" type="button" disabled={busy || (type === "article" && !channels.length)} onClick={() => void createItem()}>创建草稿</button></div></section></div>}
  </div>;
}

function Studio({ type, item, busy, onBack, onRun, onRetry, onCancel, onConfirm, onSaveManual, onUnlockCopy, onSaveVisualLayout, onUploadVisual }: { type: ContentType; item: ContentItem; busy: boolean; onBack: () => void; onRun: (task: string, branchId?: string, instruction?: string) => Promise<void>; onRetry: (job: GenerationJob) => Promise<void>; onCancel: (job: GenerationJob) => Promise<void>; onConfirm: (node: string, branchId?: string) => Promise<void>; onSaveManual: (task: string, output: Record<string, unknown>, branchId?: string) => Promise<void>; onUnlockCopy: () => Promise<void>; onSaveVisualLayout: (layout: Pick<VisualLayout, "template" | "palette" | "blockOrder" | "hiddenBlockIds">) => Promise<void>; onUploadVisual: (file: File) => Promise<void> }) {
  return <div className="page-stack"><button className="back-link studio-back" type="button" onClick={onBack}>← 返回项目列表</button><section className="panel studio-summary"><div><span className="eyebrow">CONTENT DRAFT</span><h2>{item.title}</h2><p>修订 {item.revision} · {item.productId ? "使用已确认产品" : "使用人工素材"}</p></div><span className="status-badge">{item.status}</span></section>
    {type === "image" && <ImageStudio item={item} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} onUnlockCopy={onUnlockCopy} onSaveVisualLayout={onSaveVisualLayout} onUploadVisual={onUploadVisual} />}
    {type === "article" && <div className="branch-grid">{item.branches.map((branch) => <ArticleBranch key={branch.id} item={item} branch={branch} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} />)}</div>}
    {type === "training" && <TrainingStudio item={item} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} />}
    <JobPanel item={item} jobs={item.jobs} busy={busy} onRetry={onRetry} onCancel={onCancel} />
  </div>;
}

function ImageStudio({ item, busy, onRun, onConfirm, onSaveManual, onUnlockCopy, onSaveVisualLayout, onUploadVisual }: any) {
  const copy = item.payload.copy;
  const confirmed = item.payload.copyStatus === "confirmed";
  const visual = item.payload.visual as VisualDraft | undefined;
  return <div className="image-studio-stack">
    <section className="panel studio-node">
      <div className="panel__heading"><div><span className="eyebrow">COPY FIRST</span><h2>图片文案确认</h2></div><span className="status-badge">{confirmed ? "已确认" : copy ? "待确认" : "未生成"}</span></div>
      {copy ? <pre className="structured-output">{pretty(copy)}</pre> : <p className="studio-hint">生成标题、卖点、区块结构和必要免责声明；确认文案前不会产生图片费用。</p>}
      <ManualJsonEditor task="image_copy" value={copy} disabled={busy || confirmed} onSave={(output) => onSaveManual("image_copy", output)} />
      <div className="node-actions">
        <button className="button button--secondary" type="button" disabled={busy || confirmed} onClick={() => void onRun("image_copy", undefined, copy ? "在保留事实的前提下优化表达" : "")}>{copy ? "重新生成文案" : "生成图片文案"}</button>
        {copy && !confirmed && <button className="button" type="button" disabled={busy} onClick={() => void onConfirm("copy")}>确认文案</button>}
        {confirmed && <button className="button button--secondary" type="button" disabled={busy} onClick={() => void onUnlockCopy()}>解锁修改文案</button>}
        <button className="button" type="button" disabled={busy || !confirmed} title={!confirmed ? "请先确认图片文案" : "千问只生成无关键文字视觉素材"} onClick={() => void onRun("image_visual")}>{visual ? "重新生成视觉素材" : "生成视觉素材"}</button>
      </div>
    </section>
    {visual ? <VisualEditor key={`${visual.assetId}-${item.revision}`} visual={visual} busy={busy} onSave={onSaveVisualLayout} onUpload={onUploadVisual} /> : <section className="panel visual-empty"><div><span className="eyebrow">DETERMINISTIC LAYOUT</span><h2>图片结构化编辑器</h2><p>确认文案后生成视觉素材。模型不承载关键文字，标题、卖点、产品名和免责声明由程序确定性排版。</p></div><label className={`button button--secondary ${!confirmed || busy ? "is-disabled" : ""}`}>上传自有替换图<input className="visually-hidden" type="file" accept="image/png,image/jpeg" disabled={!confirmed || busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void onUploadVisual(file); event.currentTarget.value = ""; }} /></label></section>}
  </div>;
}

function VisualEditor({ visual, busy, onSave, onUpload }: { visual: VisualDraft; busy: boolean; onSave: (layout: Pick<VisualLayout, "template" | "palette" | "blockOrder" | "hiddenBlockIds">) => Promise<void>; onUpload: (file: File) => Promise<void> }) {
  const [draft, setDraft] = useState<VisualLayout>(visual.layout);
  const blocks = new Map(draft.blocks.map((block) => [block.id, block]));
  const visibleBlocks = draft.blockOrder.map((id) => blocks.get(id)).filter((block) => block && !draft.hiddenBlockIds.includes(block.id));
  const move = (id: string, offset: number) => {
    setDraft((current) => {
      const order = [...current.blockOrder];
      const index = order.indexOf(id), target = index + offset;
      if (index < 0 || target < 0 || target >= order.length) return current;
      [order[index], order[target]] = [order[target], order[index]];
      return { ...current, blockOrder: order };
    });
  };
  const toggle = (id: string, visible: boolean) => setDraft((current) => ({ ...current, hiddenBlockIds: visible ? current.hiddenBlockIds.filter((item) => item !== id) : [...current.hiddenBlockIds, id] }));
  return <section className="panel visual-editor">
    <div className="panel__heading"><div><span className="eyebrow">VISUAL COMPOSER</span><h2>图片结构化编辑器</h2></div><span className={`status-badge ${visual.status === "needs_relayout" ? "status-badge--warning" : ""}`}>{visual.status === "needs_relayout" ? "需重新排版" : "草稿预览"}</span></div>
    {visual.status === "needs_relayout" && <div className="visual-warning" role="status">文案已解锁或发生变化。旧视觉仍保留，但重新确认文案并保存布局前不得进入后续审核。</div>}
    <div className="visual-editor-grid">
      <div className={`visual-canvas visual-canvas--${draft.template} visual-canvas--${draft.palette}`} style={{ "--brand-primary": draft.brandColors.primary, "--brand-secondary": draft.brandColors.secondary } as React.CSSProperties}>
        <img src={`/api/v1/visual-assets/${encodeURIComponent(visual.assetId)}/preview`} alt="AI 生成的无关键文字视觉素材" />
        <div className="visual-overlay">{visibleBlocks.map((block) => block && <div className={`visual-block visual-block--${block.type}`} key={block.id} data-block-id={block.id}>{block.text}</div>)}</div>
      </div>
      <div className="visual-controls">
        <div className="visual-selects"><label className="field"><span>模板</span><select aria-label="图片模板" value={draft.template} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, template: event.target.value as VisualLayout["template"] }))}><option value="long-editorial">长图编辑版</option><option value="square-focus">方形营销海报</option><option value="header-split">公众号头图</option></select></label><label className="field"><span>配色</span><select aria-label="图片配色" value={draft.palette} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, palette: event.target.value as VisualLayout["palette"] }))}><option value="brand">品牌紫青</option><option value="ocean">专业海蓝</option><option value="forest">稳健森林</option><option value="sunset">温暖日落</option></select></label></div>
        <div className="visual-block-list"><strong>区块顺序与显隐</strong>{draft.blockOrder.map((id, index) => { const block = blocks.get(id); if (!block) return null; const visible = !draft.hiddenBlockIds.includes(id); return <div key={id}><label><input type="checkbox" checked={visible} disabled={busy || block.required} onChange={(event) => toggle(id, event.target.checked)} /><span>{block.type} {block.required && "· 必需"}</span></label><div><button type="button" aria-label={`上移 ${id}`} disabled={busy || index === 0} onClick={() => move(id, -1)}>↑</button><button type="button" aria-label={`下移 ${id}`} disabled={busy || index === draft.blockOrder.length - 1} onClick={() => move(id, 1)}>↓</button></div></div>; })}</div>
        <div className="node-actions"><button className="button" type="button" disabled={busy} onClick={() => void onSave({ template: draft.template, palette: draft.palette, blockOrder: draft.blockOrder, hiddenBlockIds: draft.hiddenBlockIds })}>保存图片布局</button><label className={`button button--secondary ${busy ? "is-disabled" : ""}`}>上传替换图<input className="visually-hidden" type="file" accept="image/png,image/jpeg" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void onUpload(file); event.currentTarget.value = ""; }} /></label></div>
      </div>
    </div>
  </section>;
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
  if (job.taskType === "image_copy") return item.payload.copyStatus === "confirmed";
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
