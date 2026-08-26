import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, AppError } from "../lib/api";
import type { AssetDetail, AssetSummary, ContentItem } from "../types";

const typeLabel = { image: "图片", article: "文章", training: "培训" } as const;
const channelLabel = { wechat: "公众号", xiaohongshu: "小红书" } as const;

export function AssetLibraryPage() {
  const [items, setItems] = useState<AssetSummary[]>([]);
  const [active, setActive] = useState<AssetDetail | null>(null);
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState<ContentItem | null>(null);

  async function loadList(nextType = type, nextStatus = status, nextChannel = channel) {
    setLoading(true); setError(null);
    try {
      const query = new URLSearchParams();
      if (nextType) query.set("type", nextType);
      if (nextStatus) query.set("status", nextStatus);
      if (nextChannel) query.set("channel", nextChannel);
      const result = await apiGet<{ items: AssetSummary[] }>(`/assets${query.size ? `?${query}` : ""}`);
      setItems(result.items);
    } catch (cause) { setError(cause instanceof AppError ? cause.message : "素材库读取失败"); }
    finally { setLoading(false); }
  }

  async function openAsset(id: string) {
    setBusy(true); setError(null); setNotice(""); setCopied(null);
    try { setActive(await apiGet<AssetDetail>(`/assets/${encodeURIComponent(id)}`)); }
    catch (cause) { setError(cause instanceof AppError ? cause.message : "素材详情读取失败"); }
    finally { setBusy(false); }
  }

  async function duplicate() {
    if (!active) return;
    setBusy(true); setError(null); setNotice("");
    try {
      const result = await apiJson<ContentItem>(`/assets/${encodeURIComponent(active.id)}/duplicate`, "POST");
      setCopied(result); setNotice("已复制为独立草稿，后续修改不会影响已批准素材");
    } catch (cause) { setError(cause instanceof AppError ? cause.message : "复制草稿失败"); }
    finally { setBusy(false); }
  }

  useEffect(() => { void loadList(); }, []);

  if (loading && !active) return <LoadingState label="正在读取批准素材" />;
  if (error && !active && items.length === 0) return <ErrorState message={error} onRetry={() => void loadList()} />;

  return <div className="page-stack">
    <PageHeader eyebrow="ASSET LIBRARY" title="素材库" description="合规审核通过的不可变版本自动归档。可安全预览、按类型与渠道筛选、下载正式文件，或复制为新的独立草稿。" />
    {(error || notice) && <div className={error ? "feedback-bar feedback-bar--error" : "feedback-bar"} role={error ? "alert" : "status"}>{error ?? notice}{copied && <Link to={`/create/${copied.type}`}>打开草稿列表 →</Link>}</div>}
    {active ? <AssetDetailView asset={active} busy={busy} onBack={() => { setActive(null); setError(null); setNotice(""); setCopied(null); }} onDuplicate={duplicate} /> : <>
      <section className="panel asset-filters" aria-label="素材筛选">
        <label className="field"><span>素材类型</span><select aria-label="素材类型" value={type} onChange={(event) => { const value = event.target.value; setType(value); void loadList(value, status, channel); }}><option value="">全部类型</option><option value="image">图片</option><option value="article">文章</option><option value="training">培训</option></select></label>
        <label className="field"><span>素材状态</span><select aria-label="素材状态" value={status} onChange={(event) => { const value = event.target.value; setStatus(value); void loadList(type, value, channel); }}><option value="">全部状态</option><option value="approved">已批准</option></select></label>
        <label className="field"><span>发布渠道</span><select aria-label="发布渠道" value={channel} onChange={(event) => { const value = event.target.value; setChannel(value); void loadList(type, status, value); }}><option value="">全部渠道</option><option value="wechat">公众号</option><option value="xiaohongshu">小红书</option></select></label>
      </section>
      {items.length ? <section className="asset-grid" aria-label="批准素材列表">{items.map((item) => <button type="button" className="asset-card" key={item.id} onClick={() => void openAsset(item.id)}><span className="status-badge status-badge--approved">已批准</span><span className="eyebrow">{typeLabel[item.type]}{item.channel ? ` · ${channelLabel[item.channel]}` : ""}</span><strong>{item.title}</strong><p>不可变版本 V{item.contentVersion}</p><small>{new Date(item.updatedAt).toLocaleString("zh-CN")}</small><i>安全预览与下载 →</i></button>)}</section> : <EmptyState title="当前筛选下没有批准素材" description="内容依次通过产品事实审核与合规审核后，会自动进入素材库。" action={<Link className="button button--secondary" to="/reviews">前往审核中心</Link>} />}
    </>}
  </div>;
}

function AssetDetailView({ asset, busy, onBack, onDuplicate }: { asset: AssetDetail; busy: boolean; onBack: () => void; onDuplicate: () => Promise<void> }) {
  const download = `/api/v1/assets/${encodeURIComponent(asset.id)}/download`;
  return <div className="page-stack">
    <button className="back-link" type="button" onClick={onBack}>← 返回素材库</button>
    <section className="panel asset-detail-head"><div><span className="eyebrow">{typeLabel[asset.type]}{asset.channel ? ` · ${channelLabel[asset.channel]}` : ""}</span><h2>{asset.title}</h2><p>批准素材 · 不可变版本 V{asset.contentVersion} · {new Date(asset.submittedAt).toLocaleString("zh-CN")}</p></div><span className="status-badge status-badge--approved">已批准</span></section>
    <div className="asset-detail-grid">
      <section className="panel"><div className="panel__heading"><div><span className="eyebrow">SAFE PREVIEW</span><h2>批准版本预览</h2></div></div><AssetPreview asset={asset} /></section>
      <aside className="panel asset-actions"><span className="eyebrow">EXPORT</span><h2>正式使用</h2><p>下载文件由批准快照即时生成，不读取后续草稿修改。图片会把关键文案叠加到 PNG；文章和培训导出为 DOCX。</p><a className="button" href={download} download>下载{asset.type === "image" ? " PNG" : " DOCX"}</a><button className="button button--secondary" type="button" disabled={busy} onClick={() => void onDuplicate()}>复制为新草稿</button></aside>
    </div>
  </div>;
}

function AssetPreview({ asset }: { asset: AssetDetail }) {
  const snapshot = asset.snapshot ?? {};
  if (asset.type === "image") {
    const visual = snapshot.payload?.visual ?? {};
    const layout = visual.layout ?? {};
    const hidden = new Set<string>(layout.hiddenBlockIds ?? []);
    const blocks = new Map<string, Record<string, any>>((layout.blocks ?? []).map((block: Record<string, any>) => [String(block.id), block]));
    const ordered = (layout.blockOrder ?? []).map((id: string) => blocks.get(id)).filter((block: Record<string, any> | undefined) => block && !hidden.has(String(block.id)));
    return <div className="asset-image-preview"><img src={`/api/v1/visual-assets/${encodeURIComponent(String(visual.assetId ?? ""))}/preview`} alt={`${asset.title}批准版本预览`} /><div className="asset-image-preview__copy">{ordered.map((block: Record<string, any>) => <p className={`asset-preview-block asset-preview-block--${block.type ?? "text"}`} key={block.id}>{String(block.text ?? "")}</p>)}</div></div>;
  }
  if (asset.type === "article") {
    const body = snapshot.branch?.body ?? {};
    return <article className="asset-document-preview"><h1>{body.title ?? asset.title}</h1>{(body.paragraphs ?? []).map((paragraph: string, index: number) => <p key={index}>{paragraph}</p>)}<h3>免责声明</h3><p>{body.disclaimer ?? "具体责任以保险合同为准。"}</p></article>;
  }
  const script = snapshot.payload?.script ?? {};
  return <div className="asset-document-preview"><h1>{asset.title}</h1>{(script.chapters ?? []).map((chapter: Record<string, any>, index: number) => <section key={index}><h2>第 {index + 1} 章 {String(chapter.title ?? "")}</h2><p>{String(chapter.narration ?? "")}</p><aside><strong>知识卡</strong><p>{String(chapter.knowledgeCard ?? "")}</p></aside></section>)}<h2>课程测试</h2><ol>{(script.questions ?? []).map((question: Record<string, any>, index: number) => <li key={index}>{String(question.question ?? "")}</li>)}</ol></div>;
}
