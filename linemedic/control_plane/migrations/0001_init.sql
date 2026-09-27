-- 0001_init: spec/docs/04-data-state.md §5 DDL 원문 그대로 (W06). 아래 구분선까지 한 글자도 바꾸지 않는다.
-- DDL을 바꿔야 하면 DECISIONS.md에 먼저 적고 새 번호의 migration 파일로 추가한다(tasks/W06 금지·함정).
-- ===== spec 04 §5 원문 시작 =====
PRAGMA foreign_keys = ON;

CREATE TABLE demo_runs (
  id TEXT PRIMARY KEY,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  created_at TEXT NOT NULL,
  config_json TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_run ON demo_runs(active) WHERE active=1;

CREATE TABLE incidents (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  fingerprint_version TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK(source_kind IN ('LOG','GITHUB_ISSUE','OPERATOR','VERIFIER')),
  status TEXT NOT NULL CHECK(status IN (
    'NEW','INVESTIGATING','VALIDATING','PR_OPENED','DEPLOYING','VERIFYING',
    'WORK_ORDER_DRAFTED','RESOLVED','ESCALATED','EXECUTION_UNKNOWN')),
  category TEXT CHECK(category IS NULL OR category IN (
    'code_bug','equipment','config','infra','external_dependency','unknown')),
  service TEXT NOT NULL,
  line_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  count INTEGER NOT NULL DEFAULT 0 CHECK(count>=0),
  attempt_id TEXT,
  attempt_deadline TEXT,
  submissions INTEGER NOT NULL DEFAULT 0 CHECK(submissions>=0 AND submissions<=2),
  reason_code TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  reopened_from TEXT REFERENCES incidents(id),
  details_json TEXT NOT NULL,
  UNIQUE(run_id,id)
);
CREATE UNIQUE INDEX one_active_fingerprint ON incidents(run_id,fingerprint)
WHERE status IN ('NEW','INVESTIGATING','VALIDATING','PR_OPENED','DEPLOYING','VERIFYING','EXECUTION_UNKNOWN');

CREATE TABLE github_issues (
  repository_id INTEGER NOT NULL,
  issue_number INTEGER NOT NULL CHECK(issue_number>0),
  node_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','closed')),
  author_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  last_observed_at TEXT NOT NULL,
  PRIMARY KEY(repository_id,issue_number),
  UNIQUE(repository_id,node_id)
);
CREATE TABLE issue_bindings (
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  fingerprint_version TEXT NOT NULL,
  problem_fingerprint TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  basis TEXT NOT NULL CHECK(basis IN ('CREATED','MANAGED_RECEIPT','STRUCTURED_APPROVED','OPERATOR')),
  decision_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(routing_scope,repository_id,fingerprint_version,problem_fingerprint),
  FOREIGN KEY(repository_id,issue_number) REFERENCES github_issues(repository_id,issue_number)
);
CREATE TABLE work_items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  routing_scope TEXT NOT NULL,
  repository_id INTEGER NOT NULL,
  issue_number INTEGER NOT NULL,
  generation INTEGER NOT NULL CHECK(generation>0),
  status TEXT NOT NULL CHECK(status IN (
    'WAITING_APPROVAL','WAITING_NOTIFICATION','READY','RUNNING','WAITING_REVIEW',
    'WAITING_VERIFICATION','HANDED_OFF','SUCCEEDED','BLOCKED','CANCELLED','EXECUTION_UNKNOWN')),
  version INTEGER NOT NULL DEFAULT 0,
  issue_snapshot_sha256 TEXT NOT NULL,
  authorization_json TEXT NOT NULL,
  start_notification_id TEXT,
  attempt_id TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
  reason_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  details_json TEXT NOT NULL,
  UNIQUE(routing_scope,repository_id,issue_number,generation),
  UNIQUE(run_id,incident_id),
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id),
  FOREIGN KEY(repository_id,issue_number) REFERENCES github_issues(repository_id,issue_number)
);
CREATE UNIQUE INDEX one_active_work_per_issue
ON work_items(routing_scope,repository_id,issue_number)
WHERE status NOT IN ('HANDED_OFF','SUCCEEDED','BLOCKED','CANCELLED');
CREATE UNIQUE INDEX one_running_work ON work_items((1)) WHERE status='RUNNING';

CREATE TABLE integration_state (
  integration_id TEXT NOT NULL,
  state_key TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(integration_id,state_key)
);
CREATE TABLE api_requests (
  principal_scope TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  idempotency_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('RECEIVED','COMPLETED','UNKNOWN')),
  response_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(principal_scope,method,path,run_id,idempotency_key)
);
CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source_identity TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT NOT NULL REFERENCES work_items(id),
  attempt_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('RECEIVED','CHECKING','ALLOWED','REJECTED')),
  received_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  checks_json TEXT NOT NULL,
  UNIQUE(run_id,incident_id,attempt_id,idempotency_key),
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE executions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  proposal_id TEXT REFERENCES proposals(id),
  operation TEXT NOT NULL CHECK(operation IN ('CREATE_ISSUE','CREATE_PR','DRAFT_WORK_ORDER','DEPLOY')),
  logical_key TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('INTENDED','RUNNING','SUCCEEDED','FAILED','UNKNOWN')),
  stage TEXT NOT NULL,
  intended_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  request_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE verifications (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  execution_id TEXT REFERENCES executions(id),
  origin TEXT NOT NULL CHECK(origin IN ('agent_release','human_injected_negative','manual_integration')),
  verdict TEXT NOT NULL CHECK(verdict IN ('RUNNING','PASS','FAIL','INCONCLUSIVE')),
  contract_id TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES demo_runs(id),
  incident_id TEXT,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE notifications (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  event_type TEXT NOT NULL CHECK(event_type IN (
    'WORK_STARTING','WORK_BLOCKED','PR_READY','HANDOFF_DRAFTED',
    'RECOVERY_VERIFIED','RECOVERY_NOT_VERIFIED','WORK_CANCELLED')),
  route_id TEXT NOT NULL,
  logical_key TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('PENDING','SENDING','ACCEPTED','FAILED','UNKNOWN')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
  next_attempt_at TEXT,
  receipt_id TEXT,
  accepted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
CREATE TABLE case_notes (
  id TEXT PRIMARY KEY,
  series_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  supersedes_id TEXT REFERENCES case_notes(id),
  repository_id INTEGER NOT NULL,
  service TEXT NOT NULL,
  problem_fingerprint TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_incident_id TEXT NOT NULL,
  work_id TEXT REFERENCES work_items(id),
  source_event_key TEXT NOT NULL UNIQUE,
  outcome TEXT NOT NULL CHECK(outcome IN (
    'VERIFIED_SUCCESS','VERIFIED_FAILURE','UNVERIFIED','BLOCKED','INCONCLUSIVE','HANDOFF')),
  phase TEXT NOT NULL,
  origin TEXT NOT NULL,
  publish_status TEXT NOT NULL CHECK(publish_status IN ('DRAFT','PUBLISHED','RETRACTED')),
  observed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(series_id,revision),
  FOREIGN KEY(source_run_id,source_incident_id) REFERENCES incidents(run_id,id)
);
CREATE INDEX case_lookup ON case_notes(repository_id,service,problem_fingerprint,publish_status);
CREATE TABLE case_retrievals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  work_id TEXT NOT NULL REFERENCES work_items(id),
  mode TEXT NOT NULL CHECK(mode IN ('cold_start','memory_assisted')),
  snapshot_id TEXT,
  engine TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('OK','NO_HIT','DISABLED','UNAVAILABLE')),
  query_json TEXT NOT NULL,
  results_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id,incident_id) REFERENCES incidents(run_id,id)
);
-- ===== spec 04 §5 원문 끝 =====

-- migration 적용 기록 (W06). version은 파일 이름의 번호다.
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY CHECK(version>0),
  applied_at TEXT NOT NULL
);
