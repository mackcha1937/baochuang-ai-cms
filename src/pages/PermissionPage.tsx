import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { apiGet, AppError } from "../lib/api";
import type { PermissionMatrix } from "../types";

export function PermissionPage() {
  const [matrix, setMatrix] = useState<PermissionMatrix | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    setLoading(true); setError(null);
    try { setMatrix(await apiGet<PermissionMatrix>("/permissions")); }
    catch (reason) { setError(reason instanceof AppError ? reason.message : "权限矩阵加载失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  const descriptions = useMemo(() => new Map(matrix?.permissions.map((item) => [item.code, item.description]) ?? []), [matrix]);
  if (loading) return <LoadingState label="正在读取权限矩阵" />;
  if (error || !matrix) return <ErrorState message={error ?? "权限数据为空"} onRetry={() => void load()} />;

  return <div className="page-stack permission-page">
    <PageHeader eyebrow="ACCESS CONTROL" title="用户与权限" description="本地演示版以只读矩阵展示服务端权限契约；不提供登录、邀请码或角色修改。" actions={<span className="readonly-badge">只读演示模式</span>} />
    <section className="permission-boundary"><strong>边界说明</strong><p>超级演示用户同时持有四个角色，便于本地连续演示。后端仍对每个操作校验权限码，只读访客的越权请求会被 403 拒绝并写入审计。正式多用户与邀请码不属于本地 MVP。</p></section>
    <section><div className="section-heading"><div><span className="eyebrow">DEMO USERS</span><h2>演示用户</h2></div><p>{matrix.users.length} 个固定测试身份，不可在界面中增删。</p></div><div className="permission-user-grid">{matrix.users.map((user) => <article className="panel" key={user.id}><div className="permission-user-head"><span className="user-profile__avatar">{user.name.slice(0, 1)}</span><div><span className="status-badge">{user.id === matrix.currentUserId ? "当前用户" : "反向测试用户"}</span><h3>{user.name}</h3><code>{user.id}</code></div></div><div className="permission-user-facts"><span><strong>{user.roles.length}</strong>个角色</span><span><strong>{user.permissions.length}</strong>项有效权限</span></div><div className="permission-tags">{user.roles.map((role) => <code key={role}>{role}</code>)}{!user.roles.length && <small>未分配角色</small>}</div></article>)}</div></section>
    <section className="panel"><div className="panel__heading"><div><span className="eyebrow">ROLE MATRIX</span><h2>角色与权限码矩阵</h2></div><span>{matrix.roles.length} 个角色 · {matrix.permissions.length} 个权限码</span></div><div className="role-matrix" role="table" aria-label="角色权限矩阵">{matrix.roles.map((role) => <div className="role-matrix__row" role="row" key={role.name}><div role="rowheader"><strong>{role.name}</strong><small>{role.permissions.length ? `${role.permissions.length} 项权限` : "无操作权限"}</small></div><div role="cell">{role.permissions.length ? role.permissions.map((code) => <span className="permission-code" key={code}><code>{code}</code><small>{descriptions.get(code)}</small></span>) : <span className="permission-empty">仅用于验证未认证/无权路径</span>}</div></div>)}</div></section>
    <section className="panel permission-catalog"><div className="panel__heading"><div><span className="eyebrow">SERVER CONTRACT</span><h2>权限码说明</h2></div><span>服务端逐请求校验</span></div>{matrix.permissions.map((item) => <div key={item.code}><code>{item.code}</code><p>{item.description}</p></div>)}</section>
  </div>;
}
