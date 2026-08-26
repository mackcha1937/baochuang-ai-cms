import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import { navigationItems } from "../navigation";

export function AppShell({ children }: { children: ReactNode }) {
  const { data } = useAppContext();
  const [collapsed, setCollapsed] = useState(false);
  const aiConfigured = data?.serviceStatus.deepseek === "configured" || data?.serviceStatus.qwen === "configured";

  return (
    <div className={collapsed ? "app-layout app-layout--collapsed" : "app-layout"}>
      <aside className="sidebar">
        <div className="brand-mark">
          <div className="brand-mark__logo" aria-hidden="true">B</div>
          <div className="brand-mark__copy">
            <strong>保创AI</strong>
            <span>BAOCHUANG AI</span>
          </div>
        </div>
        <nav className="main-nav" aria-label="一级导航">
          {navigationItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              className={({ isActive }) => isActive ? "nav-item nav-item--active" : "nav-item"}
              title={collapsed ? item.label : undefined}
            >
              <span className="nav-icon" aria-hidden="true">{item.shortLabel}</span>
              <span className="nav-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <button className="sidebar-toggle" type="button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "展开导航" : "收起导航"}>
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
          <span className="nav-label">收起导航</span>
        </button>
      </aside>
      <section className="app-main">
        <header className="topbar">
          <div className="topbar__status">
            <span className="demo-pill">本地演示</span>
            <span className="service-pill"><i className={aiConfigured ? "dot dot--ready" : "dot"} />AI 服务{aiConfigured ? "已配置" : "未配置"}</span>
          </div>
          <div className="user-chip">
            <span className="user-chip__avatar">超</span>
            <div><strong>{data?.user.name ?? "超级演示用户"}</strong><span>全权限演示模式</span></div>
          </div>
        </header>
        <main className="content">{children}</main>
      </section>
    </div>
  );
}
