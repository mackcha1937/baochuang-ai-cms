import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../components/States";
import { apiGet, AppError } from "../lib/api";
import type { ProductDetail } from "../types";

export function ProductDetailPage() {
  const { id = "" } = useParams();
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true); setError(null);
    try { setProduct(await apiGet<ProductDetail>(`/products/${encodeURIComponent(id)}`)); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "产品详情加载失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [id]);

  if (loading) return <LoadingState label="正在读取产品详情" />;
  if (error || !product) return <div className="page-stack"><Link className="back-link" to="/products">← 返回产品资料库</Link><ErrorState message={error ?? "产品数据为空"} onRetry={() => void load()} /></div>;

  return (
    <div className="page-stack">
      <Link className="back-link" to="/products">← 返回产品资料库</Link>
      <section className="product-hero">
        <div className="product-monogram product-monogram--large">D12</div>
        <div className="product-hero__copy"><div><span className="status-badge">{product.status}</span><span className="version-badge">{product.version} 当前版本</span></div><span className="eyebrow">{product.category} · {product.insurer}</span><h1>{product.name}</h1><p>{product.summary}</p></div>
        <button className="button" type="button" disabled title="Stage 01 不包含产品更新">更新产品 · 后续开放</button>
      </section>
      <section className="detail-layout">
        <div className="detail-main page-stack">
          <article className="panel"><div className="panel__heading"><div><span className="eyebrow">CONFIRMED FACTS</span><h2>关键事实</h2></div><span className="status-badge">{product.facts.filter((fact) => fact.confirmed).length} 项已确认</span></div><dl className="facts-grid">{product.facts.map((fact) => <div key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd><span aria-label={fact.confirmed ? "已确认" : "待确认"}>✓</span></div>)}</dl></article>
          <article className="panel"><div className="panel__heading"><div><span className="eyebrow">SOURCE DOCUMENTS</span><h2>资料清单</h2></div></div><div className="document-list">{product.documents.map((document) => <div key={document.name}><span className="document-type">{document.type}</span><div><strong>{document.name}</strong><p>{document.status}</p></div><span className={document.status === "已解析" ? "status-badge" : "status-badge status-badge--muted"}>{document.status}</span></div>)}</div></article>
        </div>
        <aside className="detail-sidebar">
          <article className="panel"><span className="eyebrow">PRODUCT HIGHLIGHTS</span><h2>产品特色</h2><ol className="feature-list">{product.features.map((feature, index) => <li key={feature}><span>{String(index + 1).padStart(2, "0")}</span><p>{feature}</p></li>)}</ol></article>
          <article className="notice-card"><strong>关于演示数据</strong><p>当前产品为 Stage 01 预置数据，只用于验证应用骨架和持久化。后续创作仍需经过事实确认与审核。</p></article>
        </aside>
      </section>
    </div>
  );
}
