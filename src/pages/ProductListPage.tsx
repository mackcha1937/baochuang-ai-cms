import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { apiGet, apiJson, AppError } from "../lib/api";
import type { ProductDetail, ProductSummary } from "../types";

export function ProductListPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ProductSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

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

  async function create(allowDuplicate = false) {
    if (!newName.trim()) {
      setCreateError("请输入产品名称");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      const product = await apiJson<ProductDetail>("/products", "POST", { name: newName.trim(), allowDuplicate });
      setShowCreate(false);
      navigate(`/products/${product.id}`);
    } catch (reason) {
      if (reason instanceof AppError && reason.code === "DUPLICATE_PRODUCT") {
        if (window.confirm("已存在同名产品。仍要创建一份新的草稿吗？")) await create(true);
      } else {
        setCreateError(reason instanceof AppError ? reason.message : "产品草稿创建失败");
      }
    } finally {
      setCreating(false);
    }
  }

  useEffect(() => { void load(""); }, []);

  return (
    <div className="page-stack">
      <PageHeader eyebrow="PRODUCT KNOWLEDGE" title="产品资料库" description="上传并解析产品资料，人工核对关键字段后形成可追溯的确认版本。" actions={<button className="button" type="button" onClick={() => { setNewName(""); setCreateError(null); setShowCreate(true); }}>＋ 新建产品</button>} />
      <section className="panel toolbar-panel">
        <form className="search-form" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label htmlFor="product-search">搜索产品</label>
          <div><input id="product-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入产品名、保险公司或类别" /><button className="button button--secondary" type="submit" disabled={loading}>搜索</button></div>
        </form>
        <div className="filter-note"><span className="status-badge">数据持久化</span><p>草稿、文件和解析结果在刷新或重启后保留。</p></div>
      </section>
      {loading ? <LoadingState label="正在读取产品资料" /> : error ? <ErrorState message={error} onRetry={() => void load()} /> : items.length === 0 ? (
        <EmptyState title="没有匹配的产品" description="尝试清空搜索词，或新建一份产品草稿。" action={<button className="button button--secondary" type="button" onClick={() => { setQuery(""); void load(""); }}>清空搜索</button>} />
      ) : (
        <section className="product-grid" aria-label="产品列表">
          {items.map((product) => (
            <Link className="product-card" to={`/products/${product.id}`} key={product.id}>
              <div className="product-card__top"><span className="product-monogram">{product.name.slice(0, 2)}</span><span className={product.status === "草稿" ? "status-badge status-badge--draft" : "status-badge"}>{product.status}</span></div>
              <div><span className="eyebrow">{product.category || "类别待填写"}</span><h2>{product.name}</h2><p>{product.insurer || "保险公司待填写"}</p></div>
              <dl><div><dt>当前版本</dt><dd>{product.version}</dd></div><div><dt>资料来源</dt><dd>{product.sourceType}</dd></div></dl>
              <span className="text-link">{product.status === "草稿" ? "继续完善" : "查看产品详情"} <i>→</i></span>
            </Link>
          ))}
        </section>
      )}
      {showCreate && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !creating) setShowCreate(false); }}>
        <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="create-product-title">
          <span className="eyebrow">NEW PRODUCT DRAFT</span><h2 id="create-product-title">新建产品草稿</h2><p>先创建可保存的草稿，再上传 PDF、DOCX 或 XLSX 资料。</p>
          <label className="field"><span>产品名称</span><input autoFocus maxLength={150} value={newName} onChange={(event) => setNewName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void create(); }} placeholder="例如：XX重大疾病保险" /></label>
          {createError && <div className="inline-error" role="alert">{createError}</div>}
          <div className="modal-actions"><button className="button button--secondary" type="button" disabled={creating} onClick={() => setShowCreate(false)}>取消</button><button className="button" type="button" disabled={creating} onClick={() => void create()}>{creating ? "正在创建…" : "创建并继续"}</button></div>
        </section>
      </div>}
    </div>
  );
}
