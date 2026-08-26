import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, AppError, uploadProductFile } from "../lib/api";
import type { ProductDetail, ProductDocument } from "../types";

const statusText: Record<string, string> = {
  queued: "等待解析", parsing: "解析中", parsed: "解析完成", failed: "解析失败",
  needs_manual: "需要人工粘贴", manual_ready: "人工原文已保存", cancelled: "已取消",
};

function cloneProduct(product: ProductDetail): ProductDetail {
  return JSON.parse(JSON.stringify(product)) as ProductDetail;
}

export function ProductDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const activeCancel = useRef<(() => void) | null>(null);
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [draft, setDraft] = useState<ProductDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [manualId, setManualId] = useState<string | null>(null);
  const [manualText, setManualText] = useState("");

  async function load() {
    setLoading(true); setError(null);
    try {
      const result = await apiGet<ProductDetail>(`/products/${encodeURIComponent(id)}`);
      setProduct(result); setDraft(cloneProduct(result)); setDirty(false);
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : "产品详情加载失败");
    } finally { setLoading(false); }
  }

  async function saveChanges(): Promise<ProductDetail | null> {
    if (!draft || draft.status === "已确认") return product;
    setSaving(true); setError(null); setNotice(null);
    try {
      const result = await apiJson<ProductDetail>(`/products/${encodeURIComponent(id)}`, "PATCH", {
        name: draft.name, insurer: draft.insurer, category: draft.category, summary: draft.summary,
        revision: draft.revision, facts: draft.facts,
      });
      setProduct(result); setDraft(cloneProduct(result)); setDirty(false); setNotice("产品字段已保存");
      return result;
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : "产品字段保存失败");
      return null;
    } finally { setSaving(false); }
  }

  function mutate(updater: (current: ProductDetail) => void) {
    setDraft((current) => {
      if (!current) return current;
      const next = cloneProduct(current); updater(next); return next;
    });
    setDirty(true); setNotice(null);
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    if (dirty && !await saveChanges()) return;
    for (const file of Array.from(files)) {
      let uploadError: string | null = null;
      setUploading(`正在上传 ${file.name}`); setError(null); setNotice(null);
      const task = uploadProductFile(id, file);
      activeCancel.current = task.cancel;
      try {
        const document = await task.promise as ProductDocument;
        activeCancel.current = null;
        setUploading(`正在解析 ${file.name}`);
        await load();
        await apiJson(`/products/${encodeURIComponent(id)}/documents/${document.id}/parse`, "POST");
        setNotice(`${file.name} 已上传并完成解析`);
      } catch (reason) {
        if (reason instanceof AppError && reason.code === "REQUEST_CANCELLED") setNotice(`${file.name} 上传已取消`);
        else uploadError = reason instanceof AppError ? reason.message : `${file.name} 上传失败`;
      } finally {
        activeCancel.current = null; setUploading(null); await load();
        if (uploadError) setError(uploadError);
      }
    }
    if (fileInput.current) fileInput.current.value = "";
  }

  async function documentAction(document: ProductDocument, action: "retry" | "cancel" | "delete") {
    if (!document.id) return;
    if (action === "delete" && !window.confirm(`确认删除“${document.name}”及其解析记录吗？`)) return;
    setError(null); setNotice(null);
    try {
      if (action === "delete") await apiJson(`/products/${id}/documents/${document.id}`, "DELETE");
      else await apiJson(`/products/${id}/documents/${document.id}/${action}`, "POST");
      setNotice(action === "delete" ? "文件已删除" : action === "retry" ? "已完成重试" : "任务已取消");
      await load();
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "文件操作失败"); }
  }

  async function saveManual(document: ProductDocument) {
    if (!document.id || !manualText.trim()) { setError("请先粘贴可核对的原文"); return; }
    try {
      await apiJson(`/products/${id}/documents/${document.id}/manual-text`, "PUT", { text: manualText });
      setManualId(null); setManualText(""); setNotice("人工原文已保存"); await load();
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "人工原文保存失败"); }
  }

  async function confirmProduct() {
    let current = draft;
    if (dirty) current = await saveChanges();
    if (!current || !window.confirm("确认所有关键字段已经人工核对，并锁定当前产品版本吗？")) return;
    try {
      const result = await apiJson<ProductDetail>(`/products/${id}/confirm`, "POST", { revision: current.revision });
      setProduct(result); setDraft(cloneProduct(result)); setNotice("产品已确认，当前版本进入只读状态");
    } catch (reason) {
      if (reason instanceof AppError && reason.code === "CONFIRMATION_INCOMPLETE") {
        const missing = reason.details?.missing as string[] | undefined;
        setError(`${reason.message}${missing?.length ? `：${missing.join("、")}` : ""}`);
      } else setError(reason instanceof AppError ? reason.message : "产品确认失败");
    }
  }

  async function deleteDraft() {
    if (!product || !window.confirm(`确认删除草稿“${product.name}”及其全部上传文件吗？`)) return;
    try { await apiJson(`/products/${id}`, "DELETE"); navigate("/products"); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "草稿删除失败"); }
  }

  useEffect(() => { void load(); }, [id]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  if (loading && !product) return <LoadingState label="正在读取产品详情" />;
  if (error && !product) return <div className="page-stack"><Link className="back-link" to="/products">← 返回产品资料库</Link><ErrorState message={error} onRetry={() => void load()} /></div>;
  if (!product || !draft) return null;

  const editable = product.status !== "已确认";
  const confirmedCount = draft.facts.filter((fact) => fact.confirmed && fact.value.trim()).length;
  const sourceDocuments = product.documents.filter((document) => !document.legacy);

  return (
    <div className="page-stack">
      <Link className="back-link" to="/products" onClick={(event) => { if (dirty && !window.confirm("尚有未保存修改，确认返回并放弃吗？")) event.preventDefault(); }}>← 返回产品资料库</Link>
      <section className="product-hero product-hero--compact">
        <div className="product-monogram product-monogram--large">{product.name.slice(0, 2)}</div>
        <div className="product-hero__copy"><div><span className={editable ? "status-badge status-badge--draft" : "status-badge"}>{product.status}</span><span className="version-badge">{product.version} · 修订 {product.revision}</span></div><span className="eyebrow">{product.category || "类别待填写"} · {product.insurer || "保险公司待填写"}</span><h1>{product.name}</h1><p>{product.summary || "完善产品字段，上传源文件并确认关键事实。"}</p></div>
        {editable && <div className="hero-actions"><button className="button button--light" type="button" disabled={!dirty || saving} onClick={() => void saveChanges()}>{saving ? "保存中…" : dirty ? "保存修改" : "已保存"}</button><button className="button button--ghost-light" type="button" onClick={() => void confirmProduct()}>确认产品</button></div>}
      </section>
      {(error || notice || uploading) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}><span>{error ?? notice ?? uploading}</span>{uploading && activeCancel.current && <button type="button" onClick={() => activeCancel.current?.()}>取消上传</button>}</div>}

      {editable ? <section className="detail-layout detail-layout--editor">
        <div className="detail-main page-stack">
          <article className="panel"><div className="panel__heading"><div><span className="eyebrow">PRODUCT FIELDS</span><h2>产品基本字段</h2></div><span className={dirty ? "status-badge status-badge--warning" : "status-badge"}>{dirty ? "有未保存修改" : "已保存"}</span></div>
            <div className="form-grid">
              <label className="field field--wide"><span>产品名称 *</span><input value={draft.name} maxLength={150} onChange={(event) => mutate((value) => { value.name = event.target.value; })} /></label>
              <label className="field"><span>保险公司 *</span><input value={draft.insurer} maxLength={100} onChange={(event) => mutate((value) => { value.insurer = event.target.value; })} placeholder="填写承保公司" /></label>
              <label className="field"><span>产品类别 *</span><input value={draft.category} maxLength={100} onChange={(event) => mutate((value) => { value.category = event.target.value; })} placeholder="例如：重大疾病保险" /></label>
              <label className="field field--wide"><span>产品摘要 *</span><textarea value={draft.summary} maxLength={3000} rows={4} onChange={(event) => mutate((value) => { value.summary = event.target.value; })} placeholder="根据源资料人工概括产品定位和核心保障" /></label>
            </div>
          </article>
          <article className="panel"><div className="panel__heading"><div><span className="eyebrow">KEY FACT CONFIRMATION</span><h2>关键字段人工确认</h2></div><span className="status-badge">{confirmedCount}/{draft.facts.length} 已确认</span></div>
            <div className="fact-editor">{draft.facts.map((fact, index) => <div key={`${fact.label}-${index}`}><label className="field"><span>{fact.label} {fact.required ? "*" : ""}</span><input value={fact.value} onChange={(event) => mutate((value) => { value.facts[index].value = event.target.value; value.facts[index].confirmed = false; })} placeholder={`填写${fact.label}`} /></label><label className="confirm-check"><input type="checkbox" checked={fact.confirmed} disabled={!fact.value.trim()} onChange={(event) => mutate((value) => { value.facts[index].confirmed = event.target.checked; })} /><span>已对照原始资料人工确认</span></label></div>)}</div>
          </article>
        </div>
        <aside className="detail-sidebar">
          <article className="panel upload-panel"><span className="eyebrow">SOURCE UPLOAD</span><h2>上传产品资料</h2><p>支持 PDF、DOCX、XLSX，单文件不超过 20 MB。扩展名、MIME 和真实文件结构会同时校验。</p><input ref={fileInput} className="visually-hidden" type="file" multiple accept=".pdf,.docx,.xlsx" onChange={(event) => void uploadFiles(event.target.files)} /><button className="button" type="button" disabled={Boolean(uploading)} onClick={() => fileInput.current?.click()}>选择文件</button></article>
          <article className="notice-card"><strong>确认门禁</strong><p>需填写基本字段、逐项确认关键事实，并至少保留一份解析成功或人工粘贴的源资料。</p></article>
          <button className="danger-button" type="button" onClick={() => void deleteDraft()}>删除当前草稿</button>
        </aside>
      </section> : <section className="panel"><div className="panel__heading"><div><span className="eyebrow">CONFIRMED FACTS</span><h2>已确认关键事实</h2></div><span className="status-badge">只读版本</span></div><dl className="facts-grid">{product.facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd><span aria-label="已确认">✓</span></div>)}</dl></section>}

      <section className="panel"><div className="panel__heading"><div><span className="eyebrow">SOURCE DOCUMENTS</span><h2>上传与解析状态</h2></div><span className="readonly-badge">{sourceDocuments.length} 份用户资料</span></div>
        {sourceDocuments.length === 0 ? <div className="compact-empty"><strong>尚未上传源资料</strong><p>选择 PDF、DOCX 或 XLSX 后，文件会先安全入库再解析。</p></div> : <div className="document-list document-list--managed">{sourceDocuments.map((document) => <div key={document.id ?? document.name}>
          <span className="document-type">{document.type}</span><div className="document-copy"><strong>{document.name}</strong><p>{document.sizeBytes ? `${(document.sizeBytes / 1024).toFixed(1)} KB · ` : ""}{statusText[document.status] ?? document.status}{document.attempt ? ` · 第 ${document.attempt} 次解析` : ""}</p>{document.parseError && <small>{document.parseError}</small>}{document.structure && document.status === "parsed" && <small>{document.type === "PDF" ? `${String(document.structure.pageCount ?? 0)} 页` : document.type === "DOCX" ? `${String(document.structure.paragraphCount ?? 0)} 段 / ${String(document.structure.tableCount ?? 0)} 表` : `${String(document.structure.sheetCount ?? 0)} 个工作表`}</small>}</div>
          <span className={`status-badge status-badge--${document.status}`}>{statusText[document.status] ?? document.status}</span>
          {editable && <div className="document-actions">{["failed", "needs_manual", "cancelled"].includes(document.status) && <button type="button" onClick={() => void documentAction(document, "retry")}>重试</button>}{["queued", "parsing", "failed", "needs_manual"].includes(document.status) && <button type="button" onClick={() => void documentAction(document, "cancel")}>取消</button>}{["failed", "needs_manual", "cancelled"].includes(document.status) && <button type="button" onClick={() => { setManualId(document.id); setManualText(document.manualText ?? ""); }}>粘贴原文</button>}<button className="text-danger" type="button" onClick={() => void documentAction(document, "delete")}>删除</button></div>}
          {manualId === document.id && <div className="manual-fallback"><label className="field"><span>人工粘贴原文</span><textarea rows={6} value={manualText} onChange={(event) => setManualText(event.target.value)} placeholder="粘贴可追溯的产品条款或规则原文" /></label><div><button className="button button--secondary" type="button" onClick={() => setManualId(null)}>取消</button><button className="button" type="button" onClick={() => void saveManual(document)}>保存原文</button></div></div>}
        </div>)}</div>}
      </section>
    </div>
  );
}
