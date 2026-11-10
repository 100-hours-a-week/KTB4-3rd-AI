-- 모니터 스키마 V001 (원문: 최종문서/DB/DB_ERD_MySQL.md 4절). 관리자 계정으로 SSM에서 실행한다.
CREATE TABLE request_results (
  request_id    VARCHAR(64)  NOT NULL PRIMARY KEY,
  ts            DATETIME(3)  NOT NULL,
  task_id       VARCHAR(64)  NOT NULL,
  feature       VARCHAR(32)  NOT NULL,
  result        VARCHAR(16)  NOT NULL,
  code          VARCHAR(64)  NULL,
  step_id       VARCHAR(32)  NOT NULL,
  reason        VARCHAR(64)  NULL,
  status        SMALLINT     NULL,
  error_code    VARCHAR(64)  NULL,
  route         VARCHAR(16)  NULL,
  route_reason  VARCHAR(32)  NULL,
  fallback      BOOLEAN      NOT NULL DEFAULT FALSE,
  fallback_step VARCHAR(32)  NULL,
  latency_ms    INT          NOT NULL,
  image_bytes   INT          NULL,
  image_type    VARCHAR(32)  NULL,
  image_host    VARCHAR(191) NULL,
  attempts      INT          NOT NULL DEFAULT 1,
  drill         BOOLEAN      NOT NULL DEFAULT FALSE,
  KEY idx_ts (ts),
  KEY idx_step_ts (step_id, ts)
) ENGINE=InnoDB;

CREATE TABLE llm_calls (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts            DATETIME(3)  NOT NULL,
  request_id    VARCHAR(64)  NULL,
  feature       VARCHAR(32)  NOT NULL,
  name          VARCHAR(32)  NOT NULL,
  device        VARCHAR(16)  NOT NULL,
  model         VARCHAR(64)  NOT NULL,
  prompt_ver    CHAR(8)      NOT NULL,
  ms            INT          NOT NULL,
  result        VARCHAR(16)  NOT NULL,
  reason        VARCHAR(64)  NULL,
  status        SMALLINT     NULL,
  error_code    VARCHAR(64)  NULL,
  input_tokens  INT          NULL,
  output_tokens INT          NULL,
  attempts      TINYINT      NOT NULL DEFAULT 1,
  openai_req    VARCHAR(64)  NULL,
  KEY idx_ts (ts),
  KEY idx_feature_ts (feature, ts),
  KEY idx_request (request_id)
) ENGINE=InnoDB;

CREATE TABLE app_logs (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts         DATETIME(3)  NOT NULL,
  task_id    VARCHAR(64)  NOT NULL,
  level      VARCHAR(10)  NOT NULL,
  logger     VARCHAR(128) NOT NULL,
  head       VARCHAR(191) NOT NULL,
  message    TEXT         NOT NULL,
  request_id VARCHAR(64)  NULL,
  exc        TEXT         NULL,
  KEY idx_ts (ts),
  KEY idx_head_ts (head, ts),
  KEY idx_request (request_id),
  FULLTEXT KEY ft_message (message) WITH PARSER ngram
) ENGINE=InnoDB;

CREATE TABLE resource_samples (
  task_id    VARCHAR(64) NOT NULL,
  ts         DATETIME(3) NOT NULL,
  mem_bytes  BIGINT      NOT NULL,
  rss_bytes  BIGINT      NOT NULL,
  inflight   INT         NOT NULL,
  PRIMARY KEY (task_id, ts)
) ENGINE=InnoDB;

CREATE TABLE monitor_health (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts         DATETIME(3)  NOT NULL,
  task_id    VARCHAR(64)  NOT NULL,
  dropped    INT NOT NULL DEFAULT 0,
  orphan     INT NOT NULL DEFAULT 0,
  send_fail  INT NOT NULL DEFAULT 0,
  restarts   INT NOT NULL DEFAULT 0,
  rss_bytes  BIGINT       NOT NULL,
  image_tag  VARCHAR(128) NOT NULL,
  KEY idx_ts (ts)
) ENGINE=InnoDB;

CREATE TABLE metrics_1m (
  bucket     DATETIME     NOT NULL,
  feature    VARCHAR(32)  NOT NULL,
  step_id    VARCHAR(32)  NOT NULL,
  calls      INT    NOT NULL DEFAULT 0,
  fails      INT    NOT NULL DEFAULT 0,
  fallbacks  INT    NOT NULL DEFAULT 0,
  tokens     BIGINT NOT NULL DEFAULT 0,
  p95_ms     INT    NULL,
  PRIMARY KEY (bucket, feature, step_id)
) ENGINE=InnoDB;

CREATE TABLE alerts (
  alert_key  VARCHAR(191) NOT NULL PRIMARY KEY,
  last_at    DATETIME(3)  NOT NULL,
  `rank`     TINYINT      NOT NULL,
  `count`    INT          NOT NULL,
  held       INT          NOT NULL DEFAULT 0,
  task_id    VARCHAR(64)  NOT NULL
) ENGINE=InnoDB;

CREATE TABLE runtime_flags (
  `key`          VARCHAR(128) PRIMARY KEY,
  value          JSON         NOT NULL,
  expires_at     DATETIME(3)  NULL,
  set_by         VARCHAR(64)  NOT NULL,
  reason         VARCHAR(255) NULL,
  adjustment_id  VARCHAR(32)  NULL
) ENGINE=InnoDB;

CREATE TABLE scheduled_events (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  event       JSON         NOT NULL,
  not_before  DATETIME(3)  NOT NULL,
  claimed_by  VARCHAR(64)  NULL,
  claimed_at  DATETIME(3)  NULL,
  done_at     DATETIME(3)  NULL,
  KEY idx_due (not_before, claimed_by)
) ENGINE=InnoDB;

CREATE TABLE tg_state (
  stage           VARCHAR(16) PRIMARY KEY,
  last_update_id  BIGINT      NOT NULL
) ENGINE=InnoDB;

CREATE TABLE tg_members (
  user_id       BIGINT       NOT NULL PRIMARY KEY,
  chat_id       BIGINT       NOT NULL,
  name          VARCHAR(128) NULL,
  username      VARCHAR(64)  NULL,
  status        ENUM('pending','approved') NOT NULL,
  requested_at  DATETIME(3)  NOT NULL,
  decided_at    DATETIME(3)  NULL,
  decided_by    BIGINT       NULL,
  KEY idx_status (status)
) ENGINE=InnoDB;

CREATE TABLE schema_version (
  version      INT          PRIMARY KEY,
  description  VARCHAR(191) NOT NULL,
  applied_at   DATETIME(3)  NOT NULL
) ENGINE=InnoDB;

CREATE TABLE incidents (
  id               VARCHAR(32)  PRIMARY KEY,
  created_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  trigger_key      VARCHAR(191) NOT NULL,
  feature          VARCHAR(32)  NULL,
  step_id          VARCHAR(32)  NULL,
  summary          TEXT         NOT NULL,
  cause_text       TEXT         NOT NULL,
  resolution_text  TEXT         NULL,
  confidence       ENUM('low','medium','high') NOT NULL,
  evidence         JSON         NOT NULL,
  knowledge_refs   JSON         NOT NULL,
  knowledge_id     VARCHAR(32)  NULL,
  policy_key       VARCHAR(64)  NULL,
  status           ENUM('open','resolved','dismissed') NOT NULL DEFAULT 'open',
  drill            BOOLEAN      NOT NULL DEFAULT FALSE,
  agent_run_id     VARCHAR(32)  NULL,
  resolved_at      DATETIME(3)  NULL,
  KEY idx_status (status),
  KEY idx_created (created_at),
  KEY idx_knowledge (knowledge_id)
) ENGINE=InnoDB;

CREATE TABLE chat_messages (
  id                   BIGINT AUTO_INCREMENT PRIMARY KEY,
  chat_id              BIGINT       NOT NULL,
  message_id           BIGINT       NULL,
  reply_to_message_id  BIGINT       NULL,
  role                 ENUM('user','bot') NOT NULL,
  text                 TEXT         NOT NULL,
  incident_id          VARCHAR(32)  NULL,
  intent               VARCHAR(32)  NULL,
  evidence             JSON         NULL,
  embed_pending        BOOLEAN      NOT NULL DEFAULT FALSE,
  ts                   DATETIME(3)  NOT NULL,
  UNIQUE KEY uq_msg (chat_id, message_id),
  KEY idx_chat_ts (chat_id, ts),
  KEY idx_incident (incident_id)
) ENGINE=InnoDB;

CREATE TABLE chat_summaries (
  chat_id     BIGINT       PRIMARY KEY,
  summary     TEXT         NOT NULL,
  upto_id     BIGINT       NOT NULL,
  updated_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;

CREATE TABLE adjustments (
  id              VARCHAR(32)  PRIMARY KEY,
  incident_id     VARCHAR(32)  NULL,
  knowledge_id    VARCHAR(32)  NULL,
  knob            VARCHAR(64)  NOT NULL,
  old_value       JSON         NOT NULL,
  new_value       JSON         NOT NULL,
  ttl_min         INT          NOT NULL,
  status          ENUM('proposed','rejected','applied','kept','rolled_back','expired') NOT NULL,
  before_metrics  JSON         NULL,
  after_metrics   JSON         NULL,
  verdict         ENUM('improved','no_effect','worse') NULL,
  created_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  applied_at      DATETIME(3)  NULL,
  ended_at        DATETIME(3)  NULL,
  KEY idx_status (status)
) ENGINE=InnoDB;

CREATE TABLE agent_runs (
  run_id            VARCHAR(32)  PRIMARY KEY,
  event_id          VARCHAR(64)  NOT NULL,
  ts                DATETIME(3)  NOT NULL,
  event_type        VARCHAR(64)  NOT NULL,
  route             VARCHAR(16)  NULL,
  drill             BOOLEAN      NOT NULL DEFAULT FALSE,
  node_path         JSON         NOT NULL,
  input_tokens      INT          NOT NULL DEFAULT 0,
  output_tokens     INT          NOT NULL DEFAULT 0,
  tokens            INT          NOT NULL DEFAULT 0,
  ms                INT          NOT NULL DEFAULT 0,
  tool_ms           INT          NOT NULL DEFAULT 0,
  detect_to_send_ms INT          NULL,
  outcome           VARCHAR(32)  NOT NULL,
  error             VARCHAR(255) NULL,
  image_tag         VARCHAR(128) NOT NULL,
  UNIQUE KEY uq_event (event_id),
  KEY idx_ts (ts)
) ENGINE=InnoDB;

CREATE TABLE tg_messages (
  message_id  BIGINT       NOT NULL,
  chat_id     BIGINT       NOT NULL,
  ref_kind    ENUM('incident','alert','draft','adjustment','answer','member','doc') NOT NULL,
  ref_id      VARCHAR(255) NOT NULL,
  ts          DATETIME(3)  NOT NULL,
  PRIMARY KEY (message_id, chat_id, ref_kind)
) ENGINE=InnoDB;

CREATE TABLE drill_runs (
  id             VARCHAR(32)  PRIMARY KEY,
  scenario_id    VARCHAR(16)  NOT NULL,
  `group`        ENUM('A','B','C') NOT NULL,
  started_by     BIGINT       NOT NULL,
  status         ENUM('running','awaiting_guess','closed') NOT NULL,
  human_guess_1  TEXT NULL,
  human_ms_1     INT  NULL,
  human_guess_2  TEXT NULL,
  human_ms_2     INT  NULL,
  human_hit_1    BOOLEAN NULL,
  human_hit_2    BOOLEAN NULL,
  human_judge_reason_1 TEXT NULL,
  human_judge_reason_2 TEXT NULL,
  started_at     DATETIME(3)  NOT NULL,
  stopped_at     DATETIME(3)  NULL
) ENGINE=InnoDB;

CREATE TABLE eval_results (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  drill_run_id          VARCHAR(32)  NOT NULL,
  scenario_id           VARCHAR(16)  NOT NULL,
  `group`               ENUM('A','B','C') NOT NULL,
  config                ENUM('single','rag','agent','detector') NOT NULL,
  run_no                TINYINT      NOT NULL,
  expected_step_id      VARCHAR(32)  NULL,
  predicted_step_id     VARCHAR(32)  NULL,
  step_hit              BOOLEAN      NULL,
  expected_cause_text   TEXT         NULL,
  predicted_cause_text  TEXT         NULL,
  cause_hit             BOOLEAN      NULL,
  judge_reason          TEXT         NULL,
  human_override        BOOLEAN      NULL,
  evidence_first_pass   BOOLEAN      NULL,
  false_positive        BOOLEAN      NOT NULL DEFAULT FALSE,
  duration_ms           INT          NULL,
  tokens                INT          NULL,
  KEY idx_drill (drill_run_id)
) ENGINE=InnoDB;

CREATE TABLE knowledge_drafts (
  id           VARCHAR(32)  PRIMARY KEY,
  kind         ENUM('knowledge','doc') NOT NULL DEFAULT 'knowledge',
  op           ENUM('add','update','delete') NOT NULL,
  target_id    VARCHAR(191) NULL,
  target_version INT        NULL,
  body         JSON         NOT NULL,
  reason       TEXT         NOT NULL,
  source_refs  JSON         NOT NULL,
  incident_id  VARCHAR(32)  NULL,
  chat_id      BIGINT       NOT NULL,
  status       ENUM('pending','saved','cancelled','expired') NOT NULL DEFAULT 'pending',
  created_at   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  expires_at   DATETIME(3)  NOT NULL,
  decided_by   BIGINT       NULL,
  decided_at   DATETIME(3)  NULL,
  KEY idx_status_exp (status, expires_at)
) ENGINE=InnoDB;

INSERT INTO schema_version (version, description, applied_at) VALUES (1, 'init', NOW(3));
