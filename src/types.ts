export interface Brand {
  id: string;
  companyName: string;
  productName: string;
  primaryColor: string;
  secondaryColor: string;
  tone: string;
  slogan: string;
  disclaimer: string;
  revision?: number;
  updatedAt?: string;
  assets?: BrandAsset[];
}

export interface BrandAsset {
  id: string;
  kind: "logo" | "image" | "article";
  name: string;
  mimeType: string;
  sizeBytes: number;
  width: number | null;
  height: number | null;
  previewText: string;
  createdAt: string;
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
  featuredProduct?: ProductSummary | null;
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
  rootProductId?: string;
  previousVersionId?: string | null;
  isCurrent?: boolean;
  effectiveAt?: string | null;
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
  rootProductId?: string;
  previousVersionId?: string | null;
  isCurrent?: boolean;
  effectiveAt?: string | null;
  versions?: ProductVersionSummary[];
  changes?: ProductChange[];
}

export interface ProductVersionSummary extends ProductSummary {
  isCurrent: boolean;
  effectiveAt: string | null;
  confirmedAt: string | null;
}

export interface ProductChange {
  field: string;
  label: string;
  before: string;
  after: string;
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
  reviewStatus?: ContentReviewStatus;
}

export type ContentReviewStatus = "draft" | "product_review" | "compliance_review" | "approved" | "rejected_product" | "rejected_compliance";

export interface ContentReviewSummary {
  id: string;
  branchId: string | null;
  contentVersion: number;
  stage: "product" | "compliance";
  status: "pending" | "in_progress" | "approved" | "rejected" | "cancelled";
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
  lastReason?: string;
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
  reviews?: ContentReviewSummary[];
  createdAt: string;
  updatedAt: string;
}

export interface ReviewRecord {
  id: string;
  taskId: string;
  actorId: string;
  decision: "start" | "approve" | "reject" | "withdraw";
  reason: string;
  createdAt: string;
}

export interface ReviewTask {
  id: string;
  contentId: string;
  branchId: string | null;
  contentVersionId: string;
  contentVersion: number;
  contentType: ContentType;
  contentTitle: string;
  branchChannel: "wechat" | "xiaohongshu" | null;
  stage: "product" | "compliance";
  status: "pending" | "in_progress" | "approved" | "rejected" | "cancelled";
  assigneeId: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
  contentRevision?: number;
  snapshot?: Record<string, any>;
  submittedBy?: string;
  submittedAt?: string;
  records?: ReviewRecord[];
}

export interface AssetSummary {
  id: string;
  contentVersionId: string;
  contentId: string;
  branchId: string | null;
  contentVersion: number;
  type: ContentType;
  channel: "wechat" | "xiaohongshu" | null;
  title: string;
  status: "approved" | "possibly_outdated" | "invalid" | "restore_pending";
  createdAt: string;
  updatedAt: string;
}

export interface AssetDetail extends AssetSummary {
  snapshot: Record<string, any>;
  submittedBy: string;
  submittedAt: string;
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
