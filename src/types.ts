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
  facts: ProductFact[];
  documents: ProductDocument[];
  revision: number;
  ownerId: string;
  createdAt: string;
  confirmedAt: string | null;
}

export interface ProductFact {
  label: string;
  value: string;
  confirmed: boolean;
  required: boolean;
}

export interface ProductDocument {
  id: string | null;
  name: string;
  originalName?: string;
  type: string;
  status: string;
  legacy: boolean;
  sizeBytes?: number;
  parseError?: string;
  attempt?: number;
  extractedText?: string;
  structure?: Record<string, unknown>;
  manualText?: string;
}

export type ContentType = "image" | "article" | "training";

export interface GenerationJob {
  id: string;
  contentId: string;
  branchId: string | null;
  taskType: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  error: string;
  errorCode: string;
  attempt: number;
  provider: string;
  model: string;
}

export interface ContentBranch {
  id: string;
  channel: "wechat" | "xiaohongshu";
  outline: Record<string, unknown>;
  outlineStatus: "empty" | "draft" | "confirmed";
  body: Record<string, unknown>;
  bodyStatus: "empty" | "draft" | "confirmed";
}

export interface ContentItem {
  id: string;
  type: ContentType;
  title: string;
  productId: string | null;
  status: string;
  revision: number;
  manualSource: string;
  settings: Record<string, unknown>;
  payload: Record<string, any>;
  trainingSession?: TrainingSession;
  branches: ContentBranch[];
  jobs: GenerationJob[];
  createdAt: string;
  updatedAt: string;
}

export interface TrainingQuizResult {
  index: number;
  question: string;
  selected: string;
  correctAnswer: string;
  correct: boolean;
  explanation: string;
}

export interface TrainingSession {
  chapterIndex: number;
  sentenceIndex: number;
  playbackRate: number;
  mode: "voice" | "silent";
  answers: Record<string, string>;
  lastScore: number | null;
  results: TrainingQuizResult[];
  attempt: number;
  status: "locked" | "ready" | "completed" | "stale";
  updatedAt: string;
}

export interface VisualBlock {
  id: string;
  type: string;
  text: string;
  required: boolean;
}

export interface VisualLayout {
  assetId: string;
  imageType: string;
  template: "long-editorial" | "square-focus" | "header-split";
  palette: "brand" | "ocean" | "forest" | "sunset";
  brandColors: { primary: string; secondary: string };
  blocks: VisualBlock[];
  blockOrder: string[];
  hiddenBlockIds: string[];
}

export interface VisualDraft {
  assetId: string;
  status: "draft" | "needs_relayout";
  layout: VisualLayout;
}
