import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { useAppContext } from "../context/AppContext";

export function PermissionPage() {
  const { data, error, loading, reload } = useAppContext();
  if (loading) return <LoadingState label="正在读取权限" />;
  if (error || !data) return <ErrorState message={error ?? "权限数据为空"} onRetry={() => void reload()} />;
  return <div className="page-stack"><PageHeader eyebrow="ACCESS CONTROL" title="用户与权限" description="本地演示版只有一个超级演示用户，不提供登录、角色切换或邀请码。" /><section className="permission-layout"><article className="panel user-profile"><div className="user-profile__avatar">超</div><div><span className="status-badge">本地演示用户</span><h2>{data.user.name}</h2><p>同时拥有四个角色，便于后续链路在本地连续演示；所有操作仍记录实际操作者。</p></div></article><article className="panel"><div className="panel__heading"><div><span className="eyebrow">ASSIGNED ROLES</span><h2>已分配角色</h2></div><span>{data.user.roles.length} 个</span></div><div className="role-grid">{data.user.roles.map((role, index) => <div key={role}><span>{String(index + 1).padStart(2, "0")}</span><strong>{role}</strong><small>已启用</small></div>)}</div></article><article className="panel"><div className="panel__heading"><div><span className="eyebrow">PERMISSION CODES</span><h2>有效权限码</h2></div><span>{data.user.permissions.length} 项</span></div><div className="permission-tags">{data.user.permissions.map((permission) => <code key={permission}>{permission}</code>)}</div></article></section></div>;
}
