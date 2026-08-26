import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="not-found">
      <span className="not-found__code" aria-hidden="true">404</span>
      <section className="empty-state" aria-labelledby="not-found-title">
        <div className="empty-orbit" aria-hidden="true"><span /></div>
        <h1 id="not-found-title">页面没有找到</h1>
        <p>地址可能已变化，当前工作状态不会受到影响。</p>
        <Link className="button" to="/">返回工作台</Link>
      </section>
    </div>
  );
}
