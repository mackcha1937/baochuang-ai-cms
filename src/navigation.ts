export interface NavigationItem {
  path: string;
  label: string;
  shortLabel: string;
  description: string;
}

export const navigationItems: NavigationItem[] = [
  { path: "/", label: "工作台", shortLabel: "台", description: "概览、快捷入口与最近动态" },
  { path: "/products", label: "产品资料库", shortLabel: "品", description: "浏览产品档案、事实和版本" },
  { path: "/create/image", label: "AI图片创作", shortLabel: "图", description: "确认文案后生成结构化产品图" },
  { path: "/create/article", label: "AI文章创作", shortLabel: "文", description: "生成公众号和小红书渠道内容" },
  { path: "/create/training", label: "AI动画培训", shortLabel: "课", description: "生成平台内互动培训课程" },
  { path: "/reviews", label: "审核中心", shortLabel: "审", description: "处理产品与合规审核任务" },
  { path: "/assets", label: "素材库", shortLabel: "材", description: "管理批准、过期与失效素材" },
  { path: "/brand", label: "品牌设置", shortLabel: "牌", description: "管理企业品牌、视觉与内容基调" },
  { path: "/permissions", label: "用户与权限", shortLabel: "权", description: "查看演示用户角色和权限" },
  { path: "/audit", label: "操作记录", shortLabel: "记", description: "追踪关键数据变化" },
];

export const futurePageConfig = {
  "/create/image": { eyebrow: "创作工具", title: "AI图片创作", action: "从产品资料库选择产品", hint: "Stage 01 已完成页面入口与状态骨架；文案确认和视觉生成将在后续阶段按门禁实现。" },
  "/create/article": { eyebrow: "创作工具", title: "AI文章创作", action: "查看可用产品", hint: "公众号与小红书会保持独立分支。Stage 01 只提供安全、明确的未启用状态。" },
  "/create/training": { eyebrow: "创作工具", title: "AI动画培训", action: "查看可用产品", hint: "课程框架、讲稿和播放器不属于 Stage 01，当前不会发起任何 AI 请求。" },
  "/reviews": { eyebrow: "内容治理", title: "审核中心", action: "返回工作台", hint: "当前没有待审核任务。Stage 01 不实现审核状态机，后续将独立开发并验收。" },
  "/assets": { eyebrow: "内容资产", title: "素材库", action: "开始创建内容", hint: "尚无批准素材。Stage 01 不提供正式素材下载，避免出现未审核内容出口。" },
} as const;
