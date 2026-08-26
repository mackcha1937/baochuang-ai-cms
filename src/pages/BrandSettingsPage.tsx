import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { useAppContext } from "../context/AppContext";

export function BrandSettingsPage() {
  const { data, error, loading, reload } = useAppContext();
  if (loading) return <LoadingState label="正在读取品牌设置" />;
  if (error || !data) return <ErrorState message={error ?? "品牌数据为空"} onRetry={() => void reload()} />;
  const fields = [["企业名称", data.brand.companyName], ["产品名称", data.brand.productName], ["内容语气", data.brand.tone], ["品牌口号", data.brand.slogan]];
  return <div className="page-stack"><PageHeader eyebrow="BRAND SYSTEM" title="品牌设置" description="Stage 01 以只读方式展示启动品牌数据；编辑与素材上传将在后续阶段验收。" /><section className="brand-layout"><article className="panel"><div className="panel__heading"><div><span className="eyebrow">BASIC PROFILE</span><h2>品牌信息</h2></div><span className="readonly-badge">只读</span></div><dl className="settings-list">{fields.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><div className="color-row"><div><span style={{ background: data.brand.primaryColor }} /><p><strong>主色</strong>{data.brand.primaryColor}</p></div><div><span style={{ background: data.brand.secondaryColor }} /><p><strong>辅助色</strong>{data.brand.secondaryColor}</p></div></div></article><aside className="brand-preview" style={{ "--preview-primary": data.brand.primaryColor } as React.CSSProperties}><span className="eyebrow eyebrow--light">BRAND PREVIEW</span><h2>{data.brand.productName}</h2><p>{data.brand.slogan}</p><small>{data.brand.disclaimer}</small></aside></section></div>;
}
