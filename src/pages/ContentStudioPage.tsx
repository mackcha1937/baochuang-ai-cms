import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, apiRequest, AppError, uploadVisualFile } from "../lib/api";
import { markdownTemplates, renderContentMarkdown } from "../lib/contentMarkdown";
import type { ContentBranch, ContentItem, ContentReviewSummary, ContentReviewStatus, ContentType, GenerationJob, ProductSummary, ReviewTask, TrainingSession, VisualDraft, VisualLayout } from "../types";

const labels = {
  image: { eyebrow: "AI IMAGE STUDIO", title: "AI图片创作", description: "先确认保险营销文案，再由千问生成无关键文字视觉素材并进行确定性排版。" },
  article: { eyebrow: "AI ARTICLE", title: "AI文章创作", description: "公众号与小红书框架独立确认，单渠道失败不影响另一渠道。" },
  training: { eyebrow: "AI TRAINING", title: "AI动画培训", description: "确认课程框架和讲稿后，在浏览器内播放中文语音、同步字幕并完成测试。" },
};

const channelLabel = { wechat: "公众号", xiaohongshu: "小红书" };
const AI_TASK_TIMEOUT_MS = 165_000;
const VISUAL_POLL_TIMEOUT_MS = 210_000;

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
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
  const reviewKeys = useRef(new Map<string, string>());

  async function loadList() {
    setLoading(true); setError(null);
    try {
      const [productResult, contentResult] = await Promise.all([
        apiGet<{ items: ProductSummary[] }>("/products"),
        apiGet<{ items: ContentItem[] }>(`/contents?type=${type}`),
      ]);
      const confirmedProducts = productResult.items.filter((product) => product.status === "已确认" && product.isCurrent !== false);
      setProducts(confirmedProducts);
      setProductId((current) => confirmedProducts.some((product) => product.id === current) ? current : confirmedProducts[0]?.id ?? "");
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

  async function saveManual(taskType: string, content: Record<string, unknown> | string, branchId?: string) {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const body = typeof content === "string" ? { markdown: content } : { output: content };
      const result = await apiJson<ContentItem>(`/contents/${active.id}/manual-output`, "PUT", { taskType, branchId: branchId ?? null, revision: active.revision, ...body });
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

  async function unlockScript() {
    if (!active || !window.confirm("解锁后可修改讲稿，当前播放进度和答题结果会标记为过期。确定继续吗？")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/unlock-script`, "POST", { revision: active.revision });
      setActive(result); setNotice("培训讲稿已解锁，既有播放会话已标记为过期");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "解锁失败"); }
    finally { setBusy(false); }
  }

  async function saveTrainingSession(session: Pick<TrainingSession, "chapterIndex" | "sentenceIndex" | "playbackRate" | "mode" | "answers">) {
    if (!active) return;
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/training-session`, "PATCH", { revision: active.revision, ...session });
      setActive(result);
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "培训进度保存失败"); }
  }

  async function submitTrainingQuiz(answers: string[]) {
    if (!active) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/training-quiz/submit`, "POST", { revision: active.revision, answers });
      setActive(result); setNotice("测试已提交，评分和逐题解析已保存");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "测试提交失败"); }
    finally { setBusy(false); }
  }

  async function resetTrainingQuiz() {
    if (!active || !window.confirm("确认清空本次答案并重新答题吗？历史尝试次数会保留。")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/training-quiz/reset`, "POST", { revision: active.revision });
      setActive(result); setNotice("测试已重置，可以重新作答");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "重新答题失败"); }
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

  async function submitReview(branchId?: string) {
    if (!active || !window.confirm("提交后将锁定当前版本并进入产品事实审核，确定继续吗？")) return;
    const action = `${active.id}:${branchId ?? "content"}:submit`;
    const key = reviewKeys.current.get(action) ?? crypto.randomUUID();
    reviewKeys.current.set(action, key);
    setBusy(true); setError(null);
    try {
      await apiRequest<ReviewTask>(`/contents/${active.id}/submit-review`, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ revision: active.revision, branchId: branchId ?? null }) });
      setActive(await apiGet<ContentItem>(`/contents/${active.id}`));
      reviewKeys.current.delete(action);
      setNotice(branchId ? "该渠道已提交产品事实审核，另一渠道仍可独立编辑" : "当前内容已提交产品事实审核");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "提交审核失败"); }
    finally { setBusy(false); }
  }

  async function withdrawReview(taskId: string) {
    if (!active || !window.confirm("仅审核尚未开始时可撤回。确定撤回本次提交吗？")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/withdraw-review`, "POST", { revision: active.revision, taskId });
      setActive(result); setNotice("提交已撤回，可以继续修改后重新提交");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "撤回审核失败"); }
    finally { setBusy(false); }
  }

  async function reopenReview(branchId?: string) {
    if (!active || !window.confirm("返回修改会解锁最终内容并创建新修订，原审核版本和记录会永久保留。确定继续吗？")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<ContentItem>(`/contents/${active.id}/reopen-review`, "POST", { revision: active.revision, branchId: branchId ?? null });
      setActive(result); setNotice("已返回修改；修复并重新确认后可提交新版本");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "返回修改失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { setActive(null); void loadList(); }, [type]);

  if (loading) return <LoadingState label={`正在读取${meta.title}项目`} />;
  if (error && !active && !showCreate) return <ErrorState message={error} onRetry={() => void loadList()} />;

  return <div className="page-stack">
    <PageHeader eyebrow={meta.eyebrow} title={meta.title} description={meta.description} actions={<button className="button" type="button" onClick={() => { setTitle(""); setManualSource(""); setError(null); setShowCreate(true); }}>＋ 新建创作</button>} />
    {(error || notice) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}>{error ?? notice}</div>}
    {active ? <Studio type={type} item={active} busy={busy} onBack={() => { setActive(null); setError(null); void loadList(); }} onRun={runTask} onRetry={retryJob} onCancel={cancelJob} onConfirm={confirm} onSaveManual={saveManual} onUnlockCopy={unlockCopy} onUnlockScript={unlockScript} onSaveVisualLayout={saveVisualLayout} onUploadVisual={uploadVisual} onSaveTrainingSession={saveTrainingSession} onSubmitTrainingQuiz={submitTrainingQuiz} onResetTrainingQuiz={resetTrainingQuiz} onSubmitReview={submitReview} onWithdrawReview={withdrawReview} onReopenReview={reopenReview} /> : items.length ? <section className="content-project-grid">{items.map((item) => <button type="button" className="content-project-card" key={item.id} onClick={() => void openItem(item.id)}><span className="status-badge">{item.status}</span><span className="eyebrow">{item.type.toUpperCase()}</span><strong>{item.title}</strong><small>修订 {item.revision} · {new Date(item.updatedAt).toLocaleString("zh-CN")}</small><i>继续创作 →</i></button>)}</section> : <EmptyState title={`还没有${meta.title}项目`} description="新建草稿后，生成失败或刷新页面都不会丢失已经完成的输入。" action={<button className="button" type="button" onClick={() => setShowCreate(true)}>新建第一个项目</button>} />}
    {showCreate && <div className="modal-backdrop"><section className="modal-card studio-create" role="dialog" aria-modal="true" aria-labelledby="content-create-title"><span className="eyebrow">NEW AI CONTENT</span><h2 id="content-create-title">新建{meta.title}草稿</h2><p>选择已确认产品，或不选择产品并填写人工素材。AI Key 只由后端读取。</p><label className="field"><span>项目标题 *</span><input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="field"><span>已确认产品</span><select value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">不使用产品档案</option>{products.map((product) => <option value={product.id} key={product.id}>{product.name}</option>)}</select></label><label className="field"><span>人工补充素材</span><textarea rows={4} value={manualSource} onChange={(event) => setManualSource(event.target.value)} placeholder="可补充受众、场景、限制或自有素材" /></label>{type === "article" && <fieldset className="channel-picker"><legend>发布渠道 *</legend>{(["wechat", "xiaohongshu"] as const).map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={(event) => setChannels((current) => event.target.checked ? [...current, channel] : current.filter((item) => item !== channel))} />{channelLabel[channel]}</label>)}</fieldset>}<div className="modal-actions"><button className="button button--secondary" type="button" disabled={busy} onClick={() => setShowCreate(false)}>取消</button><button className="button" type="button" disabled={busy || (type === "article" && !channels.length)} onClick={() => void createItem()}>创建草稿</button></div></section></div>}
  </div>;
}

function Studio({ type, item, busy, onBack, onRun, onRetry, onCancel, onConfirm, onSaveManual, onUnlockCopy, onUnlockScript, onSaveVisualLayout, onUploadVisual, onSaveTrainingSession, onSubmitTrainingQuiz, onResetTrainingQuiz, onSubmitReview, onWithdrawReview, onReopenReview }: { type: ContentType; item: ContentItem; busy: boolean; onBack: () => void; onRun: (task: string, branchId?: string, instruction?: string) => Promise<void>; onRetry: (job: GenerationJob) => Promise<void>; onCancel: (job: GenerationJob) => Promise<void>; onConfirm: (node: string, branchId?: string) => Promise<void>; onSaveManual: (task: string, content: Record<string, unknown> | string, branchId?: string) => Promise<void>; onUnlockCopy: () => Promise<void>; onUnlockScript: () => Promise<void>; onSaveVisualLayout: (layout: Pick<VisualLayout, "template" | "palette" | "blockOrder" | "hiddenBlockIds">) => Promise<void>; onUploadVisual: (file: File) => Promise<void>; onSaveTrainingSession: (session: Pick<TrainingSession, "chapterIndex" | "sentenceIndex" | "playbackRate" | "mode" | "answers">) => Promise<void>; onSubmitTrainingQuiz: (answers: string[]) => Promise<void>; onResetTrainingQuiz: () => Promise<void>; onSubmitReview: (branchId?: string) => Promise<void>; onWithdrawReview: (taskId: string) => Promise<void>; onReopenReview: (branchId?: string) => Promise<void> }) {
  const overallLocked = type !== "article" && item.status !== "draft";
  const overallComplete = type === "image" ? item.payload.copyStatus === "confirmed" && item.payload.visual?.status === "draft" : type === "training" ? item.payload.scriptStatus === "confirmed" : false;
  return <div className="page-stack"><button className="back-link studio-back" type="button" onClick={onBack}>← 返回项目列表</button><section className="panel studio-summary"><div><span className="eyebrow">CONTENT DRAFT</span><h2>{item.title}</h2><p>修订 {item.revision} · {item.productId ? "使用已确认产品" : "使用人工素材"}</p></div><span className="status-badge">{item.status}</span></section>
    {type === "image" && <ImageStudio item={item} busy={busy || overallLocked} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} onUnlockCopy={onUnlockCopy} onSaveVisualLayout={onSaveVisualLayout} onUploadVisual={onUploadVisual} />}
    {type === "article" && <div className="branch-grid">{item.branches.map((branch) => <ArticleBranch key={branch.id} item={item} branch={branch} busy={busy} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} onSubmitReview={onSubmitReview} onWithdrawReview={onWithdrawReview} onReopenReview={onReopenReview} />)}</div>}
    {type === "training" && <TrainingStudio item={item} busy={busy} editLocked={overallLocked} onRun={onRun} onConfirm={onConfirm} onSaveManual={onSaveManual} onUnlockScript={onUnlockScript} onSaveSession={onSaveTrainingSession} onSubmitQuiz={onSubmitTrainingQuiz} onResetQuiz={onResetTrainingQuiz} />}
    {type !== "article" && <ReviewWorkflowCard item={item} status={item.status as ContentReviewStatus} complete={overallComplete} busy={busy} onSubmit={() => onSubmitReview()} onWithdraw={onWithdrawReview} onReopen={() => onReopenReview()} />}
    <JobPanel item={item} jobs={item.jobs} busy={busy || overallLocked} onRetry={onRetry} onCancel={onCancel} />
  </div>;
}

function ImageStudio({ item, busy, onRun, onConfirm, onSaveManual, onUnlockCopy, onSaveVisualLayout, onUploadVisual }: any) {
  const copy = item.payload.copy;
  const confirmed = item.payload.copyStatus === "confirmed";
  const visual = item.payload.visual as VisualDraft | undefined;
  return <div className="image-studio-stack">
    <section className="panel studio-node">
      <div className="panel__heading"><div><span className="eyebrow">COPY FIRST</span><h2>图片文案确认</h2></div><span className="status-badge">{confirmed ? "已确认" : copy ? "待确认" : "未生成"}</span></div>
      {copy ? <pre className="structured-output structured-output--markdown">{renderContentMarkdown("image_copy", copy)}</pre> : <p className="studio-hint">生成 Markdown 标题、卖点、画面区块和必要免责声明；确认文案前不会产生图片费用。</p>}
      <ManualContentEditor task="image_copy" value={copy} disabled={busy || confirmed} onSave={(output) => onSaveManual("image_copy", output)} />
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

function ArticleBranch({ item, branch, busy, onRun, onConfirm, onSaveManual, onSubmitReview, onWithdrawReview, onReopenReview }: { item: ContentItem; branch: ContentBranch; busy: boolean; onRun: any; onConfirm: any; onSaveManual: any; onSubmitReview: (branchId?: string) => Promise<void>; onWithdrawReview: (taskId: string) => Promise<void>; onReopenReview: (branchId?: string) => Promise<void> }) {
  const reviewStatus = branch.reviewStatus ?? "draft";
  const locked = reviewStatus !== "draft";
  return <section className="panel studio-node">
    <div className="panel__heading"><div><span className="eyebrow">{branch.channel.toUpperCase()}</span><h2>{channelLabel[branch.channel]}分支</h2></div><span className="status-badge">{reviewStatus === "draft" ? `框架 ${branch.outlineStatus}` : reviewStatus}</span></div>
    {branch.outlineStatus === "empty" ? <p className="studio-hint">该渠道拥有独立 Markdown 框架、失败和重试状态。</p> : <pre className="structured-output structured-output--markdown">{renderContentMarkdown("article_outline", branch.outline)}</pre>}
    <ManualContentEditor task="article_outline" value={branch.outline} disabled={busy || locked || branch.outlineStatus === "confirmed"} onSave={(output) => onSaveManual("article_outline", output, branch.id)} />
    <div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || locked || branch.outlineStatus === "confirmed"} onClick={() => void onRun("article_outline", branch.id)}>{branch.outlineStatus === "empty" ? "生成 Markdown 框架" : "重新生成框架"}</button>{branch.outlineStatus === "draft" && <button className="button" type="button" disabled={busy || locked} onClick={() => void onConfirm("outline", branch.id)}>确认框架</button>}</div>
    {branch.outlineStatus === "confirmed" && <div className="article-body"><h3>完整文章</h3>{branch.bodyStatus === "empty" ? <p className="studio-hint">框架已锁定，可以生成 Markdown 全文。</p> : <pre className="structured-output structured-output--markdown">{renderContentMarkdown("article_generate", branch.body)}</pre>}<ManualContentEditor task="article_generate" value={branch.body} disabled={busy || locked || branch.bodyStatus === "confirmed"} onSave={(output) => onSaveManual("article_generate", output, branch.id)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || locked || branch.bodyStatus === "confirmed"} onClick={() => void onRun("article_generate", branch.id)}>{branch.bodyStatus === "empty" ? "生成 Markdown 全文" : "重新生成全文"}</button>{branch.bodyStatus === "draft" && <button className="button" type="button" disabled={busy || locked} onClick={() => void onConfirm("body", branch.id)}>确认全文</button>}</div></div>}
    <ReviewWorkflowCard item={item} branchId={branch.id} status={reviewStatus} complete={branch.bodyStatus === "confirmed"} busy={busy} onSubmit={() => onSubmitReview(branch.id)} onWithdraw={onWithdrawReview} onReopen={() => onReopenReview(branch.id)} />
  </section>;
}

function TrainingStudio({ item, busy, editLocked, onRun, onConfirm, onSaveManual, onUnlockScript, onSaveSession, onSubmitQuiz, onResetQuiz }: any) {
  const outline = item.payload.outline, script = item.payload.script;
  const scriptStatus = item.payload.scriptStatus ?? (item.payload.outlineStatus === "confirmed" ? "empty" : "locked");
  const playerReady = Array.isArray(script?.chapters) && script.chapters.length > 0
    && script.chapters.every((chapter: any) => chapter && typeof chapter.title === "string" && typeof chapter.narration === "string" && typeof chapter.knowledgeCard === "string")
    && Array.isArray(script?.questions) && script.questions.length === 3
    && script.questions.every((question: any) => question && typeof question.question === "string" && Array.isArray(question.options) && question.options.length >= 2 && question.options.every((option: unknown) => typeof option === "string") && typeof question.answer === "string" && typeof question.explanation === "string");
  return <div className="training-nodes">
    <section className="panel studio-node"><div className="panel__heading"><h2>01 课程框架</h2><span className="status-badge">{item.payload.outlineStatus ?? "empty"}</span></div>{outline ? <pre className="structured-output structured-output--markdown">{renderContentMarkdown("training_outline", outline)}</pre> : <p className="studio-hint">按培训对象、目的和时长生成 Markdown 章节与学习目标。</p>}<ManualContentEditor task="training_outline" value={outline} disabled={busy || editLocked || item.payload.outlineStatus === "confirmed"} onSave={(output) => onSaveManual("training_outline", output)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || editLocked || item.payload.outlineStatus === "confirmed"} onClick={() => void onRun("training_outline")}>生成 Markdown 课程框架</button>{outline && item.payload.outlineStatus !== "confirmed" && <button className="button" type="button" disabled={busy || editLocked} onClick={() => void onConfirm("outline")}>确认框架</button>}</div></section>
    <section className="panel studio-node"><div className="panel__heading"><h2>02 培训讲稿</h2><span className="status-badge">{scriptStatus}</span></div>{script ? <pre className="structured-output structured-output--markdown">{renderContentMarkdown("training_script", script)}</pre> : <p className="studio-hint">确认框架后生成 Markdown 逐章讲稿、知识卡和 3 道测试题。</p>}<ManualContentEditor task="training_script" value={script} disabled={busy || editLocked || item.payload.outlineStatus !== "confirmed" || item.payload.scriptStatus === "confirmed"} onSave={(output) => onSaveManual("training_script", output)} /><div className="node-actions"><button className="button button--secondary" type="button" disabled={busy || editLocked || item.payload.outlineStatus !== "confirmed" || item.payload.scriptStatus === "confirmed"} onClick={() => void onRun("training_script")}>生成 Markdown 培训讲稿</button>{script && item.payload.scriptStatus !== "confirmed" && <button className="button" type="button" disabled={busy || editLocked} onClick={() => void onConfirm("script")}>确认讲稿</button>}{item.payload.scriptStatus === "confirmed" && <button className="button button--secondary" type="button" disabled={busy || editLocked} onClick={() => void onUnlockScript()}>解锁修改讲稿</button>}</div></section>
    {script && item.payload.scriptStatus === "confirmed" && playerReady ? <TrainingPlayer item={item} busy={busy} onSaveSession={onSaveSession} onSubmitQuiz={onSubmitQuiz} onResetQuiz={onResetQuiz} /> : <section className="panel training-locked"><span className="eyebrow">PLAYER LOCKED</span><h2>03 互动培训播放器</h2><p>{item.payload.scriptStatus === "confirmed" && !playerReady ? "讲稿章节或 3 道测试题结构不完整，请解锁讲稿修复后重新确认。" : "人工确认讲稿后才开放语音、字幕、章节进度和测试，避免播放未核对内容。"}</p></section>}
    {item.trainingSession?.status === "stale" && <div className="visual-warning" role="status">讲稿已解锁，旧播放进度和答题结果仅作历史保留；重新确认讲稿后会建立新的播放会话。</div>}
  </div>;
}

type TrainingChapter = { title: string; narration: string; knowledgeCard: string };
type TrainingQuestion = { question: string; options: string[]; answer: string; explanation: string };

function trainingSentences(value: string) {
  const sentences = value.trim().split(/(?<=[。！？!?])\s*|\n+/).map((part) => part.trim()).filter(Boolean);
  return sentences.length ? sentences : [value.trim()].filter(Boolean);
}

function TrainingPlayer({ item, busy, onSaveSession, onSubmitQuiz, onResetQuiz }: { item: ContentItem; busy: boolean; onSaveSession: (session: Pick<TrainingSession, "chapterIndex" | "sentenceIndex" | "playbackRate" | "mode" | "answers">) => Promise<void>; onSubmitQuiz: (answers: string[]) => Promise<void>; onResetQuiz: () => Promise<void> }) {
  const script = item.payload.script as { chapters: TrainingChapter[]; questions: TrainingQuestion[] };
  const session = item.trainingSession ?? { chapterIndex: 0, sentenceIndex: 0, playbackRate: 1, mode: "voice", answers: {}, lastScore: null, results: [], attempt: 0, status: "ready", updatedAt: "" };
  const initialChapter = Math.min(session.chapterIndex, Math.max(script.chapters.length - 1, 0));
  const [chapterIndex, setChapterIndex] = useState(initialChapter);
  const [sentenceIndex, setSentenceIndex] = useState(session.sentenceIndex);
  const [playbackRate, setPlaybackRate] = useState(session.playbackRate);
  const [mode, setMode] = useState<"voice" | "silent">(session.mode);
  const [answers, setAnswers] = useState<Record<string, string>>(session.answers);
  const [playState, setPlayState] = useState<"idle" | "speaking" | "paused">("idle");
  const [playerNotice, setPlayerNotice] = useState("");
  const [quizError, setQuizError] = useState("");
  const timerRef = useRef<number | null>(null);
  const playToken = useRef(0);
  const chapter = script.chapters[chapterIndex] ?? script.chapters[0];
  const sentences = trainingSentences(chapter?.narration ?? "");
  const safeSentenceIndex = Math.min(sentenceIndex, Math.max(sentences.length - 1, 0));
  const completed = session.results.length > 0;

  useEffect(() => {
    setAnswers(session.answers);
    setQuizError("");
  }, [session.attempt, session.lastScore]);

  useEffect(() => () => {
    playToken.current += 1;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }, []);

  const persist = (next: Partial<Pick<TrainingSession, "chapterIndex" | "sentenceIndex" | "playbackRate" | "mode" | "answers">> = {}) => onSaveSession({
    chapterIndex: next.chapterIndex ?? chapterIndex,
    sentenceIndex: next.sentenceIndex ?? safeSentenceIndex,
    playbackRate: next.playbackRate ?? playbackRate,
    mode: next.mode ?? mode,
    answers: next.answers ?? answers,
  });

  function stopPlayback() {
    playToken.current += 1;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    setPlayState("idle");
  }

  function playSentence(index: number, requestedMode: "voice" | "silent" = mode) {
    playToken.current += 1;
    const token = playToken.current;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    const target = Math.min(index, Math.max(sentences.length - 1, 0));
    setSentenceIndex(target);
    setPlayState("speaking");
    void persist({ sentenceIndex: target, mode: requestedMode });

    const advance = () => {
      if (token !== playToken.current) return;
      if (target + 1 < sentences.length) playSentence(target + 1, requestedMode);
      else setPlayState("idle");
    };
    const silentFallback = (message = "") => {
      if (token !== playToken.current) return;
      setMode("silent");
      if (message) setPlayerNotice(message);
      void persist({ sentenceIndex: target, mode: "silent" });
      const duration = Math.max(900, Math.min(6000, sentences[target].length * 110 / playbackRate));
      timerRef.current = window.setTimeout(advance, duration);
    };

    if (requestedMode === "voice" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window) {
      const utterance = new SpeechSynthesisUtterance(sentences[target]);
      const voices = window.speechSynthesis.getVoices();
      const chineseVoice = voices.find((voice) => voice.lang.toLowerCase().startsWith("zh"));
      if (voices.length > 0 && !chineseVoice) {
        silentFallback("未找到中文语音，已切换为静默字幕模式");
        return;
      }
      utterance.lang = "zh-CN";
      utterance.rate = playbackRate;
      if (chineseVoice) utterance.voice = chineseVoice;
      utterance.onend = advance;
      utterance.onerror = () => silentFallback("中文语音播放失败，已从当前字幕继续静默播放");
      window.speechSynthesis.speak(utterance);
    } else {
      silentFallback(requestedMode === "voice" ? "当前浏览器不支持语音，已切换为静默字幕模式" : "");
    }
  }

  function chooseChapter(index: number) {
    stopPlayback();
    setChapterIndex(index); setSentenceIndex(0); setPlayerNotice("");
    void persist({ chapterIndex: index, sentenceIndex: 0 });
  }

  function pauseOrContinue() {
    if (playState === "speaking") {
      if (mode === "voice" && "speechSynthesis" in window) window.speechSynthesis.pause();
      else if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      setPlayState("paused");
      return;
    }
    if (playState === "paused" && mode === "voice" && "speechSynthesis" in window && window.speechSynthesis.paused) {
      window.speechSynthesis.resume(); setPlayState("speaking"); return;
    }
    playSentence(safeSentenceIndex);
  }

  function selectAnswer(index: number, answer: string) {
    const next = { ...answers, [String(index)]: answer };
    setAnswers(next); setQuizError("");
    void persist({ answers: next });
  }

  function submitQuiz() {
    const selected = script.questions.map((_, index) => answers[String(index)] ?? "");
    if (selected.some((answer) => !answer)) { setQuizError("请完成全部 3 道题后再提交"); return; }
    void onSubmitQuiz(selected);
  }

  return <section className="panel training-player" aria-label="互动培训播放器">
    <div className="panel__heading"><div><span className="eyebrow">INTERACTIVE COURSE</span><h2>03 互动培训播放器</h2></div><span className={`status-badge status-badge--${playState}`}>{playState === "speaking" ? "播放中" : playState === "paused" ? "已暂停" : completed ? "已完成测试" : "可播放"}</span></div>
    {playerNotice && <div className="training-notice" role="status">{playerNotice}</div>}
    <div className="training-player-grid">
      <nav className="training-chapters" aria-label="课程章节">{script.chapters.map((itemChapter, index) => <button type="button" className={index === chapterIndex ? "is-active" : ""} aria-current={index === chapterIndex ? "step" : undefined} onClick={() => chooseChapter(index)} key={`${itemChapter.title}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><strong>{itemChapter.title}</strong></button>)}</nav>
      <div className={`training-stage training-stage--${playState}`}><div className="training-avatar" aria-label={`黄色三维吉祥物数字讲师${playState === "speaking" ? "正在讲解" : "待机"}`}><img src="/training-presenter-v2.png" alt="" draggable="false" /></div><div className="training-subtitle" aria-live="polite"><small>{chapter?.title}</small><p>{sentences[safeSentenceIndex]}</p><span>字幕 {safeSentenceIndex + 1} / {sentences.length}</span></div></div>
      <aside className="training-knowledge"><span className="eyebrow">KNOWLEDGE CARD</span><h3>本章知识卡</h3><p>{chapter?.knowledgeCard}</p><label className="field"><span>播放语速</span><select aria-label="培训播放语速" value={playbackRate} onChange={(event) => { const next = Number(event.target.value); stopPlayback(); setPlaybackRate(next); void persist({ playbackRate: next }); }}><option value="0.75">0.75×</option><option value="1">1.0×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option></select></label><button className="button button--secondary" type="button" onClick={() => { stopPlayback(); const next = mode === "voice" ? "silent" : "voice"; setMode(next); setPlayerNotice(next === "silent" ? "已手动切换为静默字幕模式" : "已切换为中文语音模式"); void persist({ mode: next }); }}>{mode === "voice" ? "切换静默字幕" : "尝试中文语音"}</button></aside>
    </div>
    <div className="training-controls"><button className="button" type="button" onClick={() => playSentence(safeSentenceIndex)} disabled={!sentences.length}>{playState === "idle" ? "播放当前章节" : "从当前句播放"}</button><button className="button button--secondary" type="button" onClick={pauseOrContinue} disabled={playState === "idle"}>{playState === "paused" ? "继续" : "暂停"}</button><button className="button button--secondary" type="button" onClick={() => { stopPlayback(); setSentenceIndex(0); playSentence(0); }}>重播本章</button><span>{mode === "voice" ? "中文语音 + 同步字幕" : "静默字幕模式"}</span></div>
    <div className="training-quiz"><div className="training-quiz__heading"><div><span className="eyebrow">KNOWLEDGE CHECK</span><h3>课程测试 · 3 道单选题</h3></div>{session.attempt > 0 && <span>已提交 {session.attempt} 次</span>}</div>{script.questions.map((question, index) => { const result = session.results[index]; return <fieldset className={result ? (result.correct ? "is-correct" : "is-wrong") : ""} key={`${question.question}-${index}`}><legend>{index + 1}. {question.question}</legend>{question.options.map((option) => <label key={option}><input type="radio" name={`training-question-${index}`} checked={answers[String(index)] === option} disabled={busy || completed} onChange={() => selectAnswer(index, option)} />{option}</label>)}{result && <div className="training-answer"><strong>{result.correct ? "回答正确" : `回答错误 · 正确答案：${result.correctAnswer}`}</strong><p>{result.explanation}</p></div>}</fieldset>; })}{quizError && <p className="training-quiz__error" role="alert">{quizError}</p>}<div className="node-actions">{completed ? <><strong className="training-score">得分 {session.lastScore} / 3</strong><button className="button button--secondary" type="button" disabled={busy} onClick={() => void onResetQuiz()}>重新答题</button></> : <button className="button" type="button" disabled={busy} onClick={submitQuiz}>提交答案</button>}</div></div>
  </section>;
}

const reviewStatusLabel: Record<ContentReviewStatus, string> = {
  draft: "待提交", product_review: "产品审核中", compliance_review: "合规审核中",
  approved: "已批准", rejected_product: "产品审核已驳回", rejected_compliance: "合规审核已驳回",
};

function ReviewWorkflowCard({ item, branchId = null, status, complete, busy, onSubmit, onWithdraw, onReopen }: { item: ContentItem; branchId?: string | null; status: ContentReviewStatus; complete: boolean; busy: boolean; onSubmit: () => Promise<void>; onWithdraw: (taskId: string) => Promise<void>; onReopen: () => Promise<void> }) {
  const reviews = (item.reviews ?? []).filter((review) => review.branchId === branchId);
  const taskStage = status === "compliance_review" || status === "rejected_compliance" ? "compliance" : "product";
  const task = reviews.find((review) => review.stage === taskStage && (status.startsWith("rejected") ? review.status === "rejected" : ["pending", "in_progress"].includes(review.status))) as ContentReviewSummary | undefined;
  return <div className="review-workflow" aria-label={branchId ? "渠道审核流程" : "内容审核流程"}>
    <div><span className="eyebrow">TWO-STEP REVIEW</span><h3>两级审核 · {reviewStatusLabel[status]}</h3><p>{status === "draft" ? (complete ? "关键内容已确认，可以提交不可变版本。" : "完成关键节点确认后才可提交审核。") : status === "product_review" ? "产品审核员核对来源、责任、年龄、金额与限制。" : status === "compliance_review" ? "产品审核已通过，等待合规最终决定。" : status === "approved" ? "两级审核均已通过；素材入库将在下一阶段完成。" : task?.lastReason || "查看审核中心中的驳回原因，修改后重新提交新版本。"}</p></div>
    <div className="review-workflow__actions">
      {status === "draft" && <button className="button" type="button" disabled={busy || !complete} onClick={() => void onSubmit()}>提交产品审核</button>}
      {status === "product_review" && task?.status === "pending" && !task.startedAt && <button className="button button--secondary" type="button" disabled={busy} onClick={() => void onWithdraw(task.id)}>撤回提交</button>}
      {["product_review", "compliance_review"].includes(status) && <Link className="button button--secondary" to="/reviews">前往审核中心</Link>}
      {status.startsWith("rejected") && <button className="button" type="button" disabled={busy} onClick={() => void onReopen()}>按原因返回修改</button>}
      {status === "approved" && <span className="review-approved-note">审核记录已锁定</span>}
    </div>
  </div>;
}

function isJobLocked(item: ContentItem, job: GenerationJob) {
  if (job.taskType === "image_copy") return item.payload.copyStatus === "confirmed";
  if (job.taskType === "article_outline") return item.branches.find((branch) => branch.id === job.branchId)?.outlineStatus === "confirmed";
  if (job.taskType === "training_outline") return item.payload.outlineStatus === "confirmed";
  if (job.taskType === "training_script") return item.payload.scriptStatus === "confirmed";
  return false;
}

function JobPanel({ item, jobs, busy, onRetry, onCancel }: { item: ContentItem; jobs: GenerationJob[]; busy: boolean; onRetry: (job: GenerationJob) => Promise<void>; onCancel: (job: GenerationJob) => Promise<void> }) {
  return <section className="panel"><div className="panel__heading"><div><span className="eyebrow">GENERATION JOBS</span><h2>生成任务记录</h2></div></div>{jobs.length === 0 ? <p className="studio-hint">尚未创建任务。</p> : <div className="job-list">{jobs.map((job) => { const branchReviewLocked = item.type === "article" && job.branchId ? (item.branches.find((branch) => branch.id === job.branchId)?.reviewStatus ?? "draft") !== "draft" : false; const locked = isJobLocked(item, job) || branchReviewLocked || (item.type !== "article" && item.status !== "draft"); return <div key={job.id}><span className={`status-badge status-badge--${job.status}`}>{job.status}</span><strong>{job.taskType}</strong><small>attempt {job.attempt} · {job.provider}{job.model ? ` / ${job.model}` : ""}</small>{job.error && <p>{job.error}</p>}{locked && ["failed", "cancelled"].includes(job.status) && <small>{branchReviewLocked ? "该渠道已提交审核，历史任务已归档" : "节点已确认，历史任务已归档"}</small>}{!locked && ["failed", "cancelled"].includes(job.status) && <button type="button" disabled={busy} onClick={() => void onRetry(job)}>重试</button>}{["queued", "running"].includes(job.status) && <button type="button" disabled={busy || locked} onClick={() => void onCancel(job)}>取消</button>}</div>; })}</div>}</section>;
}

function ManualContentEditor({ task, value, disabled, onSave }: { task: string; value: Record<string, any> | undefined; disabled: boolean; onSave: (content: string | Record<string, unknown>) => Promise<void> }) {
  return <ManualMarkdownEditor task={task} value={value} disabled={disabled} onSave={onSave} />;
}

function ManualMarkdownEditor({ task, value, disabled, onSave }: { task: string; value: Record<string, any> | undefined; disabled: boolean; onSave: (content: string | Record<string, unknown>) => Promise<void> }) {
  const initial = renderContentMarkdown(task, value) || markdownTemplates[task] || "";
  const [text, setText] = useState(initial);
  const [localError, setLocalError] = useState<string | null>(null);
  useEffect(() => {
    setText(renderContentMarkdown(task, value) || markdownTemplates[task] || "");
    setLocalError(null);
  }, [task, value]);
  function save() {
    if (!text.trim().startsWith("# ")) { setLocalError("Markdown 必须以一级标题“# 标题”开始"); return; }
    if (/<\s*\/?\s*[A-Za-z][^>]*>/.test(text)) { setLocalError("Markdown 不允许包含 HTML 标签"); return; }
    setLocalError(null); void onSave(text.trim());
  }
  return <details className="manual-json manual-markdown"><summary>人工填写 / 编辑 Markdown</summary><p>使用标题、列表和自然段即可，不需要填写 JSON。</p><textarea aria-label={`${task} 人工内容`} rows={12} value={text} disabled={disabled} spellCheck={false} onChange={(event) => setText(event.target.value)} /><div>{localError && <span role="alert">{localError}</span>}<button className="button button--secondary" type="button" disabled={disabled} onClick={save}>保存 Markdown</button></div></details>;
}
