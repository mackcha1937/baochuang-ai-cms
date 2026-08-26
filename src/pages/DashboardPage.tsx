import { Link } from "react-router-dom";
import { ErrorState, LoadingState } from "../components/States";
import { useAppContext } from "../context/AppContext";

const quickActions = [
  { to: "/create/image", icon: "图", title: "创作产品图", text: "从准确文案开始，生成结构化视觉内容", tone: "violet" },
  { to: "/create/article", icon: "文", title: "创作渠道文章", text: "为公众号和小红书分别准备内容", tone: "cyan" },
  { to: "/create/training", icon: "课", title: "制作动画培训", text: "把产品知识转成平台内互动课程", tone: "orange" },
];

export function DashboardPage() {
  const { data, error, loading, reload } = useAppContext();
  if (loading) return <LoadingState label="正在准备工作台" />;
  if (error || !data) return <ErrorState message={error ?? "启动数据为空"} onRetry={() => void reload()} />;

  const stats = [
    ["产品档案", data.stats.products, "份已确认资料"],
    ["待审核", data.stats.pendingReviews, "项待处理任务"],
    ["批准素材", data.stats.approvedAssets, "份可用内容"],
    ["可能过期", data.stats.possiblyOutdated, "项需判断影响"],
  ];

  return (
    <div className="page-stack dashboard-page">
      <section className="hero-panel">
        <div className="hero-panel__glow" />
        <div className="hero-panel__content">
          <span className="eyebrow eyebrow--light">GOOD AFTERNOON · 本地演示工作区</span>
          <h1>让一份保险资料，生长出<br /><em>更可信的内容。</em></h1>
          <p>{data.brand.slogan}。先把事实和流程搭稳，再进入每一次创作。</p>
          <div className="hero-panel__actions">
            <Link className="button button--light" to="/products/darwin-12">查看达尔文12号</Link>
            <Link className="button button--ghost-light" to="/products">进入产品资料库</Link>
          </div>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <div className="hero-visual__ring ring-one" />
          <div className="hero-visual__ring ring-two" />
          <div className="hero-visual__card visual-card-one"><span>01</span><strong>产品事实</strong></div>
          <div className="hero-visual__card visual-card-two"><span>02</span><strong>内容创作</strong></div>
          <div className="hero-visual__card visual-card-three"><span>03</span><strong>审核资产</strong></div>
        </div>
      </section>

      <section className="stats-grid" aria-label="工作台统计">
        {stats.map(([label, value, hint]) => (
          <article className="stat-card" key={label}>
            <span>{label}</span><strong>{value}</strong><small>{hint}</small>
          </article>
        ))}
      </section>

      <section>
        <div className="section-heading"><div><span className="eyebrow">CREATE</span><h2>从这里开始</h2></div><p>三个工具互相独立，共享同一套可信产品资料。</p></div>
        <div className="quick-grid">
          {quickActions.map((item) => (
            <Link className={`quick-card quick-card--${item.tone}`} to={item.to} key={item.to}>
              <span className="quick-card__icon">{item.icon}</span>
              <div><strong>{item.title}</strong><p>{item.text}</p></div>
              <span className="quick-card__arrow" aria-hidden="true">→</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="dashboard-columns">
        <article className="panel">
          <div className="panel__heading"><div><span className="eyebrow">DATA SOURCE</span><h2>当前产品资料</h2></div><Link to="/products">查看全部</Link></div>
          <Link className="featured-product" to="/products/darwin-12">
            <div className="featured-product__symbol">D12</div>
            <div><span className="status-badge">已确认 · V1</span><strong>复星联合达尔文12号重大疾病保险（互联网）</strong><p>7 项关键事实已确认，可作为后续创作的数据来源。</p></div>
            <span aria-hidden="true">→</span>
          </Link>
        </article>
        <article className="panel">
          <div className="panel__heading"><div><span className="eyebrow">RECENT</span><h2>最近动态</h2></div><Link to="/audit">操作记录</Link></div>
          <div className="activity-list">
            {data.recentAudit.map((item) => (
              <div className="activity-item" key={item.id}><span /><div><strong>{item.detail}</strong><p>超级演示用户 · {item.occurred_at.slice(0, 10)}</p></div></div>
            ))}
          </div>
        </article>
      </section>
    </div>
  );
}
