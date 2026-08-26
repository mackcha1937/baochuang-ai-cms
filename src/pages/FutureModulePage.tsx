import { Link, useLocation } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/States";
import { futurePageConfig } from "../navigation";

export function FutureModulePage() {
  const location = useLocation();
  const config = futurePageConfig[location.pathname as keyof typeof futurePageConfig];
  if (!config) return null;
  const actionTarget = location.pathname === "/reviews" ? "/" : location.pathname === "/assets" ? "/create/article" : "/products";
  return (
    <div className="page-stack">
      <PageHeader eyebrow={config.eyebrow} title={config.title} description={config.hint} />
      <section className="panel stage-boundary">
        <div className="stage-boundary__steps" aria-label="模块实现阶段">
          <div className="stage-step stage-step--done"><span>✓</span><strong>页面入口</strong><small>Stage 01</small></div>
          <i />
          <div className="stage-step"><span>2</span><strong>业务流程</strong><small>后续阶段</small></div>
          <i />
          <div className="stage-step"><span>3</span><strong>审核资产</strong><small>后续阶段</small></div>
        </div>
        <EmptyState title="这里已经准备好，但尚未越过阶段门禁" description="页面不会伪造生成、审核或下载结果，也不会在未配置密钥时发起外部请求。" action={<Link className="button button--secondary" to={actionTarget}>{config.action}</Link>} />
      </section>
    </div>
  );
}
