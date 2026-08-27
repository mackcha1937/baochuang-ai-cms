import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { useAppContext } from "../context/AppContext";
import { apiGet, apiJson, apiRequest, AppError, uploadBrandAsset } from "../lib/api";
import type { Brand, BrandAsset } from "../types";

type BrandForm = Pick<Brand, "companyName" | "productName" | "primaryColor" | "secondaryColor" | "tone" | "slogan" | "disclaimer">;

function formFrom(brand: Brand): BrandForm {
  return {
    companyName: brand.companyName,
    productName: brand.productName,
    primaryColor: brand.primaryColor,
    secondaryColor: brand.secondaryColor,
    tone: brand.tone,
    slogan: brand.slogan,
    disclaimer: brand.disclaimer,
  };
}

function sizeLabel(bytes: number) {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function BrandSettingsPage() {
  const { data, error: bootstrapError, loading: bootstrapLoading, reload } = useAppContext();
  const [saved, setSaved] = useState<Brand | null>(null);
  const [form, setForm] = useState<BrandForm | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    setLoading(true); setError(null);
    try {
      const brand = await apiGet<Brand>("/brand");
      setSaved(brand); setForm(formFrom(brand));
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : "品牌设置加载失败");
    } finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, []);
  const dirty = useMemo(() => Boolean(saved && form && JSON.stringify(formFrom(saved)) !== JSON.stringify(form)), [saved, form]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault(); event.returnValue = "";
    };
    const guardNavigation = (event: MouseEvent) => {
      if (!dirty) return;
      const anchor = (event.target as Element | null)?.closest("a[href]") as HTMLAnchorElement | null;
      if (!anchor || anchor.target === "_blank" || anchor.href === window.location.href) return;
      if (!window.confirm("品牌设置尚未保存，确定离开并放弃修改吗？")) {
        event.preventDefault(); event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", guardNavigation, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", guardNavigation, true); };
  }, [dirty]);

  function applyBrand(brand: Brand, message: string) {
    setSaved(brand); setForm(formFrom(brand)); setNotice(message); setError(null); void reload();
  }

  function setField<K extends keyof BrandForm>(key: K, value: BrandForm[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current);
    setNotice(null);
  }

  async function save() {
    if (!saved || !form) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<Brand>("/brand", "PATCH", { ...form, revision: saved.revision ?? 1 });
      applyBrand(result, "品牌设置已保存");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "品牌设置保存失败"); }
    finally { setBusy(false); }
  }

  function cancel() {
    if (!saved) return;
    setForm(formFrom(saved)); setError(null); setNotice("已放弃未保存修改");
  }

  async function restoreDefaults() {
    if (!saved || !window.confirm("确定恢复系统默认品牌吗？当前 Logo 和参考素材会移除，历史内容不受影响。")) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<Brand>("/brand/restore-defaults", "POST", { revision: saved.revision ?? 1 });
      applyBrand(result, "已恢复系统默认品牌");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "恢复默认品牌失败"); }
    finally { setBusy(false); }
  }

  async function upload(kind: "logo" | "image" | "article", file?: File) {
    if (!saved || !file) return;
    if (dirty) { setError("请先保存或取消品牌字段修改，再上传参考素材"); return; }
    setBusy(true); setError(null);
    try {
      const result = await uploadBrandAsset(kind, saved.revision ?? 1, file) as Brand;
      applyBrand(result, `${kind === "logo" ? "Logo" : kind === "image" ? "参考图" : "参考文章"}已上传`);
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "品牌素材上传失败"); }
    finally { setBusy(false); }
  }

  async function remove(asset: BrandAsset) {
    if (!saved || !window.confirm(`确定移除「${asset.name}」吗？`)) return;
    setBusy(true); setError(null);
    try {
      const result = await apiRequest<Brand>(`/brand/assets/${encodeURIComponent(asset.id)}?revision=${saved.revision ?? 1}`, { method: "DELETE" });
      applyBrand(result, "品牌参考素材已移除");
    } catch (reason) { setError(reason instanceof AppError ? reason.message : "品牌素材移除失败"); }
    finally { setBusy(false); }
  }

  if (bootstrapLoading || loading) return <LoadingState label="正在读取品牌设置" />;
  if (bootstrapError || !data || !saved || !form) return <ErrorState message={bootstrapError ?? error ?? "品牌数据为空"} onRetry={() => void load()} />;

  const assets = saved.assets ?? [];
  const logo = assets.find((item) => item.kind === "logo");
  const images = assets.filter((item) => item.kind === "image");
  const articles = assets.filter((item) => item.kind === "article");

  return <div className="page-stack brand-page">
    <PageHeader eyebrow="BRAND SYSTEM" title="品牌设置" description="统一企业品牌、视觉基调、内容语气和合规声明。" actions={<span className="status-badge">{dirty ? "有未保存修改" : `已保存 · 修订 ${saved.revision ?? 1}`}</span>} />
    {error && <div className="inline-error" role="alert">{error}</div>}
    {notice && <div className="notice" role="status">{notice}</div>}
    <section className="brand-editor-layout">
      <article className="panel brand-form-panel">
        <div className="panel__heading"><div><span className="eyebrow">BASIC PROFILE</span><h2>品牌信息</h2></div></div>
        <div className="brand-form-grid">
          <label className="field"><span>企业名称 *</span><input value={form.companyName} maxLength={100} onChange={(event) => setField("companyName", event.target.value)} /></label>
          <label className="field"><span>平台/产品名称 *</span><input value={form.productName} maxLength={100} onChange={(event) => setField("productName", event.target.value)} /></label>
          <label className="field"><span>主色 *</span><div className="color-input"><input aria-label="品牌主色" type="color" value={form.primaryColor} onChange={(event) => setField("primaryColor", event.target.value.toUpperCase())} /><input aria-label="品牌主色值" value={form.primaryColor} pattern="#[0-9A-Fa-f]{6}" maxLength={7} onChange={(event) => setField("primaryColor", event.target.value)} /></div></label>
          <label className="field"><span>辅助色 *</span><div className="color-input"><input aria-label="品牌辅助色" type="color" value={form.secondaryColor} onChange={(event) => setField("secondaryColor", event.target.value.toUpperCase())} /><input aria-label="品牌辅助色值" value={form.secondaryColor} pattern="#[0-9A-Fa-f]{6}" maxLength={7} onChange={(event) => setField("secondaryColor", event.target.value)} /></div></label>
          <label className="field field--full"><span>内容文风 *</span><textarea rows={3} maxLength={500} value={form.tone} onChange={(event) => setField("tone", event.target.value)} /></label>
          <label className="field field--full"><span>品牌宣传语 *</span><textarea rows={2} maxLength={300} value={form.slogan} onChange={(event) => setField("slogan", event.target.value)} /></label>
          <label className="field field--full"><span>默认免责声明 *</span><textarea rows={4} maxLength={2000} value={form.disclaimer} onChange={(event) => setField("disclaimer", event.target.value)} /></label>
        </div>
        <div className="node-actions"><button className="button" type="button" disabled={busy || !dirty} onClick={() => void save()}>保存品牌</button><button className="button button--secondary" type="button" disabled={busy || !dirty} onClick={cancel}>取消修改</button><button className="button button--danger" type="button" disabled={busy} onClick={() => void restoreDefaults()}>恢复系统默认</button></div>
      </article>
      <aside className="brand-live-preview" style={{ "--preview-primary": form.primaryColor, "--preview-secondary": form.secondaryColor } as React.CSSProperties} aria-label="品牌实时预览">
        {logo ? <img className="brand-preview-logo" src={`/api/v1/brand/assets/${encodeURIComponent(logo.id)}/preview`} alt={`${form.companyName} Logo`} /> : <span className="brand-preview-monogram">{form.companyName.trim().slice(0, 1) || "B"}</span>}
        <span className="eyebrow eyebrow--light">{form.companyName || "企业名称"}</span>
        <h2>{form.productName || "品牌名称"}</h2><p>{form.slogan || "品牌宣传语"}</p>
        <blockquote>{form.tone || "内容文风"}</blockquote><small>{form.disclaimer || "默认免责声明"}</small>
      </aside>
    </section>
    <section className="panel brand-assets-panel"><div className="panel__heading"><div><span className="eyebrow">VISUAL REFERENCES</span><h2>Logo 与参考图</h2></div><p>Logo 1 张，参考图 1—3 张；支持 PNG/JPG/JPEG。</p></div>
      <div className="brand-upload-actions"><label className={`button button--secondary ${logo || busy ? "is-disabled" : ""}`}> {logo ? "Logo 已上传" : "上传 Logo"}<input aria-label="上传 Logo" hidden disabled={Boolean(logo) || busy} type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" onChange={(event) => { void upload("logo", event.target.files?.[0]); event.currentTarget.value = ""; }} /></label><label className={`button button--secondary ${images.length >= 3 || busy ? "is-disabled" : ""}`}>上传参考图（{images.length}/3）<input aria-label="上传品牌参考图" hidden disabled={images.length >= 3 || busy} type="file" accept=".png,.jpg,.jpeg,image/png,image/jpeg" onChange={(event) => { void upload("image", event.target.files?.[0]); event.currentTarget.value = ""; }} /></label></div>
      <div className="brand-reference-grid">{[...(logo ? [logo] : []), ...images].map((asset) => <article key={asset.id} className="brand-reference-card"><img src={`/api/v1/brand/assets/${encodeURIComponent(asset.id)}/preview`} alt={asset.name} /><div><span className="status-badge">{asset.kind === "logo" ? "Logo" : "参考图"}</span><strong>{asset.name}</strong><small>{asset.width} × {asset.height} · {sizeLabel(asset.sizeBytes)}</small><button className="button button--danger" type="button" disabled={busy} onClick={() => void remove(asset)}>移除</button></div></article>)}</div>
      {!logo && !images.length && <p className="studio-hint">尚未上传视觉参考，系统仍使用主/辅色生成品牌预览。</p>}
    </section>
    <section className="panel brand-assets-panel"><div className="panel__heading"><div><span className="eyebrow">WRITING REFERENCES</span><h2>参考文章</h2></div><p>上传 1—3 篇 PDF、DOCX 或 UTF-8 TXT，保留可预览文本。</p></div>
      <label className={`button button--secondary ${articles.length >= 3 || busy ? "is-disabled" : ""}`}>上传参考文章（{articles.length}/3）<input aria-label="上传品牌参考文章" hidden disabled={articles.length >= 3 || busy} type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" onChange={(event) => { void upload("article", event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
      <div className="brand-article-list">{articles.map((asset) => <article key={asset.id}><div><span className="status-badge">参考文章</span><strong>{asset.name}</strong><small>{sizeLabel(asset.sizeBytes)}</small></div><details><summary>预览解析文本</summary><p>{asset.previewText || "未解析到可预览文字"}</p></details><button className="button button--danger" type="button" disabled={busy} onClick={() => void remove(asset)}>移除</button></article>)}</div>
      {!articles.length && <p className="studio-hint">尚未上传参考文章。参考文章只用于保存企业写作样例，本阶段不自动发起 AI 调用。</p>}
    </section>
  </div>;
}
