export interface Brand {
  id: string;
  companyName: string;
  productName: string;
  primaryColor: string;
  secondaryColor: string;
  tone: string;
  slogan: string;
  disclaimer: string;
}

export interface DemoUser {
  id: string;
  name: string;
  mode: string;
  roles: string[];
  permissions: string[];
}

export interface Stats {
  products: number;
  pendingReviews: number;
  approvedAssets: number;
  possiblyOutdated: number;
}

export interface AuditItem {
  id: number;
  occurred_at: string;
  actor_id: string;
  action: string;
  object_type: string;
  object_id: string;
  detail: string;
}

export interface BootstrapData {
  brand: Brand;
  user: DemoUser;
  stats: Stats;
  serviceStatus: {
    api: "ready";
    deepseek: "configured" | "not_configured";
    qwen: "configured" | "not_configured";
  };
  recentAudit: AuditItem[];
}

export interface ProductSummary {
  id: string;
  name: string;
  insurer: string;
  category: string;
  version: string;
  status: string;
  sourceType: string;
  updatedAt: string;
}

export interface ProductDetail extends ProductSummary {
  summary: string;
  features: string[];
  facts: Array<{ label: string; value: string; confirmed: boolean }>;
  documents: Array<{ name: string; type: string; status: string }>;
}
