import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, AppError } from "../lib/api";
import type { ProductSummary } from "../types";

export function ProductListPage() {
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(search = query) {
    setLoading(true);
    setError(null);
    try {
      const result = await apiGet<{ items: ProductSummary[] }>(`/products?q=${encodeURIComponent(search)}`);
      setItems(result.items);
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : "产品列表加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(""); }, []);

  return (
    <div className="page-stack">
      <PageHeader eyebrow="PRODUCT KNOWLEDGE" title="产品资料库" description="统一查看已确认的保险产品事实、资料来源和版本。" actions={<button className="button" type="button" disabled title="Stage 01 不包含新建产品">新建产品 · 后续开放</button>} />
      <section className="panel toolbar-panel">
        <form className="search-form" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label htmlFor="product-search">搜索产品</label>
          <div><input id="product-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入产品名、保险公司或类别" /><button className="button button--secondary" type="submit" disabled={loading}>搜索</button></div>
        </form>
        <div className="filter-note"><span className="status-badge">已确认</span><p>Stage 01 仅展示预置且确认完成的产品。</p></div>
      </section>
      {loading ? <LoadingState label="正在读取产品资料" /> : error ? <ErrorState message={error} onRetry={() => void load()} /> : items.length === 0 ? (
        <EmptyState title="没有匹配的产品" description="尝试清空搜索词后重试，当前数据不会被修改。" action={<button className="button button--secondary" type="button" onClick={() => { setQuery(""); void load(""); }}>清空搜索</button>} />
      ) : (
        <section className="product-grid" aria-label="产品列表">
          {items.map((product) => (
            <Link className="product-card" to={`/products/${product.id}`} key={product.id}>
              <div className="product-card__top"><span className="product-monogram">D12</span><span className="status-badge">{product.status}</span></div>
              <div><span className="eyebrow">{product.category}</span><h2>{product.name}</h2><p>{product.insurer}</p></div>
              <dl><div><dt>当前版本</dt><dd>{product.version}</dd></div><div><dt>资料来源</dt><dd>{product.sourceType}</dd></div></dl>
              <span className="text-link">查看产品详情 <i>→</i></span>
            </Link>
          ))}
        </section>
      )}
    </div>
  );
}
