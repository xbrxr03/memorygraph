CREATE TABLE procedural_episodes (
    id TEXT PRIMARY KEY,
    bank_id TEXT NOT NULL,
    source_observation_id TEXT NOT NULL,
    task_key TEXT NOT NULL,
    strategy TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'partial', 'unknown')),
    failure TEXT,
    applicability_json TEXT NOT NULL CHECK (json_valid(applicability_json)),
    environment_json TEXT NOT NULL CHECK (json_valid(environment_json)),
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (bank_id, id),
    UNIQUE (bank_id, source_observation_id, task_key, strategy),
    CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at),
    FOREIGN KEY (bank_id) REFERENCES banks(id),
    FOREIGN KEY (bank_id, source_observation_id)
        REFERENCES observations(bank_id, id)
);

CREATE INDEX idx_procedural_task
    ON procedural_episodes(bank_id, task_key, outcome, created_at);
CREATE INDEX idx_procedural_source
    ON procedural_episodes(bank_id, source_observation_id);

CREATE VIRTUAL TABLE procedural_fts USING fts5(
    task_key,
    strategy,
    outcome,
    failure,
    applicability_json,
    environment_json,
    content='procedural_episodes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER procedural_episodes_ai AFTER INSERT ON procedural_episodes BEGIN
    INSERT INTO procedural_fts(
        rowid, task_key, strategy, outcome, failure, applicability_json, environment_json
    ) VALUES (
        new.rowid,
        new.task_key,
        new.strategy,
        new.outcome,
        COALESCE(new.failure, ''),
        new.applicability_json,
        new.environment_json
    );
END;

CREATE TRIGGER procedural_episodes_ad AFTER DELETE ON procedural_episodes BEGIN
    INSERT INTO procedural_fts(
        procedural_fts, rowid, task_key, strategy, outcome, failure,
        applicability_json, environment_json
    ) VALUES (
        'delete',
        old.rowid,
        old.task_key,
        old.strategy,
        old.outcome,
        COALESCE(old.failure, ''),
        old.applicability_json,
        old.environment_json
    );
END;

CREATE TRIGGER procedural_episodes_au AFTER UPDATE ON procedural_episodes BEGIN
    INSERT INTO procedural_fts(
        procedural_fts, rowid, task_key, strategy, outcome, failure,
        applicability_json, environment_json
    ) VALUES (
        'delete',
        old.rowid,
        old.task_key,
        old.strategy,
        old.outcome,
        COALESCE(old.failure, ''),
        old.applicability_json,
        old.environment_json
    );
    INSERT INTO procedural_fts(
        rowid, task_key, strategy, outcome, failure, applicability_json, environment_json
    ) VALUES (
        new.rowid,
        new.task_key,
        new.strategy,
        new.outcome,
        COALESCE(new.failure, ''),
        new.applicability_json,
        new.environment_json
    );
END;
