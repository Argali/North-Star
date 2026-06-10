/**
 * North Star TypeScript SDK client.
 *
 * Zero dependencies — uses the native fetch API (Node 18+ / all modern browsers).
 *
 * Usage:
 *
 *   import { NorthStarClient } from "northstar-sdk";
 *
 *   const ns = new NorthStarClient({ baseUrl: "http://localhost:8000" });
 *
 *   await ns.ingest({
 *     sourceType: "document",
 *     payload: { text: "Fleet cost review Q2 2026..." },
 *   });
 *
 *   const results = await ns.retrieve("What are the fleet maintenance costs?");
 *   console.log(results.ranked);
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ClientOptions {
  /** Base URL of the North Star API. Default: http://localhost:8000 */
  baseUrl?: string;
  /** Optional Bearer token for future auth support. */
  apiKey?: string;
  /** Request timeout in milliseconds. Default: 30 000. */
  timeoutMs?: number;
}

export interface IngestOptions {
  sourceType: "conversation" | "task" | "document";
  payload: Record<string, unknown>;
  author?: string;
  tags?: string[];
}

export interface RetrieveOptions {
  query: string;
  intent?: "knowledge" | "report" | "decision" | "entity";
  topics?: string[];
  confidenceFloor?: number;
  limit?: number;
  graphDepth?: number;
  alpha?: number;
  beta?: number;
  gamma?: number;
}

export interface ScoredItem {
  id: string;
  type: string;
  statement: string;
  confidence: number;
  topics: string[];
  scores: {
    semantic: number;
    keyword: number;
    recency: number;
    final: number;
  };
  [key: string]: unknown;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  depth: number;
  [key: string]: unknown;
}

export interface ContradictionPair {
  from_id: string;
  to_id: string;
  from_label: string;
  to_label: string;
}

export interface RetrieveResult {
  query: string;
  intent: string;
  ranked: ScoredItem[];
  graph: {
    knowledge: GraphNode[];
    reports: GraphNode[];
    decisions: GraphNode[];
    entities: GraphNode[];
    contradiction_pairs: ContradictionPair[];
    total_nodes: number;
    max_depth_reached: number;
  };
  meta: {
    alpha: number;
    beta: number;
    gamma: number;
    confidence_floor: number;
    embedding_used: boolean;
    total_candidates: number;
  };
  _cache?: "hit" | "miss";
}

export interface Report {
  id: string;
  title: string;
  author: string | null;
  context_summary: string | null;
  analysis: string | null;
  conclusions: string | null;
  tags: string[];
  created_at: string;
  [key: string]: unknown;
}

export interface Knowledge {
  id: string;
  statement: string;
  confidence: number;
  status: string;
  topics: string[];
  source_section: string | null;
  source_report_ids: string[];
  valid_from: string;
  valid_until: string | null;
  [key: string]: unknown;
}

export interface Decision {
  id: string;
  statement: string;
  rationale: string;
  status: string;
  owner: string | null;
  linked_knowledge_ids: string[];
  created_at: string;
  [key: string]: unknown;
}

export interface Entity {
  entity: Record<string, unknown>;
  graph: {
    knowledge: GraphNode[];
    decisions: GraphNode[];
    reports: GraphNode[];
    entities: GraphNode[];
    contradiction_pairs: ContradictionPair[];
    total_nodes: number;
  };
}

export class NorthStarAPIError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly detail: string,
  ) {
    super(`HTTP ${statusCode}: ${detail}`);
    this.name = "NorthStarAPIError";
  }
}

// ── Client ────────────────────────────────────────────────────────────────────

export class NorthStarClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;

  constructor(options: ClientOptions = {}) {
    this.baseUrl   = (options.baseUrl ?? "http://localhost:8000").replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.headers   = { "Content-Type": "application/json" };
    if (options.apiKey) {
      this.headers["Authorization"] = `Bearer ${options.apiKey}`;
    }
  }

  // ── Pipeline ───────────────────────────────────────────────────────────────

  /** Submit raw activity to the Scribe pipeline (async 202). */
  async ingest(options: IngestOptions): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {
      source_type: options.sourceType,
      payload: options.payload,
    };
    if (options.author != null) body["author"] = options.author;
    if (options.tags != null)   body["tags"]   = options.tags;
    return this.post("/scribe/process", body);
  }

  /** Hybrid retrieval: semantic + keyword + recency + graph traversal. */
  async retrieve(options: RetrieveOptions): Promise<RetrieveResult> {
    const params = new URLSearchParams();
    params.set("q", options.query);
    params.set("intent",      options.intent      ?? "knowledge");
    params.set("limit",       String(options.limit ?? 10));
    params.set("graph_depth", String(options.graphDepth ?? 2));
    if (options.topics)           options.topics.forEach(t => params.append("topics", t));
    if (options.confidenceFloor != null) params.set("confidence_floor", String(options.confidenceFloor));
    if (options.alpha != null)    params.set("alpha", String(options.alpha));
    if (options.beta  != null)    params.set("beta",  String(options.beta));
    if (options.gamma != null)    params.set("gamma", String(options.gamma));
    return this.get(`/retrieve?${params}`) as Promise<RetrieveResult>;
  }

  // ── Resource accessors ─────────────────────────────────────────────────────

  /** Fetch a full report by ID. */
  async report(reportId: string): Promise<Report> {
    return this.get(`/reports/${reportId}`) as Promise<Report>;
  }

  /** List reports, optionally filtered by tags. */
  async listReports(options: { tags?: string[]; limit?: number; offset?: number } = {}): Promise<{ items: Report[] }> {
    const params = new URLSearchParams();
    if (options.tags)   options.tags.forEach(t => params.append("tags", t));
    if (options.limit)  params.set("limit",  String(options.limit));
    if (options.offset) params.set("offset", String(options.offset));
    const qs = params.toString();
    return this.get(`/reports${qs ? "?" + qs : ""}`) as Promise<{ items: Report[] }>;
  }

  /** Fetch a knowledge item by ID. */
  async knowledge(knowledgeId: string): Promise<Knowledge> {
    return this.get(`/knowledge/${knowledgeId}`) as Promise<Knowledge>;
  }

  /** Fetch a decision by ID. */
  async decision(decisionId: string): Promise<Decision> {
    return this.get(`/decisions/${decisionId}`) as Promise<Decision>;
  }

  /** Fetch an entity and its related graph context. */
  async entity(entityId: string, graphDepth = 2): Promise<Entity> {
    return this.get(`/entities/${entityId}?graph_depth=${graphDepth}`) as Promise<Entity>;
  }

  /** Liveness check. */
  async health(): Promise<{ status: string }> {
    return this.get("/health") as Promise<{ status: string }>;
  }

  /** Readiness check (DB + Redis). */
  async ready(): Promise<{ status: string }> {
    return this.get("/ready") as Promise<{ status: string }>;
  }

  // ── HTTP helpers ───────────────────────────────────────────────────────────

  private async get(path: string): Promise<unknown> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(url, {
        method: "GET",
        headers: this.headers,
        signal: controller.signal,
      });
      return this.handle(resp);
    } finally {
      clearTimeout(timer);
    }
  }

  private async post(path: string, body: unknown): Promise<unknown> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      return this.handle(resp);
    } finally {
      clearTimeout(timer);
    }
  }

  private async handle(resp: Response): Promise<unknown> {
    if (!resp.ok) {
      const detail = await resp.text().catch(() => resp.statusText);
      throw new NorthStarAPIError(resp.status, detail);
    }
    return resp.json();
  }
}
