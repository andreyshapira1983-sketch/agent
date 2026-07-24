-- Project Intelligence schema v1
-- Connection layer must enable PRAGMA foreign_keys=ON on every open.

PRAGMA foreign_keys = ON;

CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE scan_runs (
    id                      TEXT PRIMARY KEY,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    repository_commit_sha   TEXT NOT NULL,
    repository_ref          TEXT,
    worktree_state          TEXT NOT NULL
        CHECK (worktree_state IN ('clean', 'dirty')),
    dirty_paths_hash        TEXT,
    schema_version          TEXT NOT NULL,
    extractor_version       TEXT NOT NULL,
    resolver_version        TEXT NOT NULL,
    status                  TEXT NOT NULL
        CHECK (status IN ('running', 'success', 'failed')),
    stats                   JSON CHECK (stats IS NULL OR json_valid(stats)),
    error_summary           TEXT
);

CREATE TABLE logical_sources (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL
        CHECK (type IN ('document', 'file', 'artifact', 'other')),
    path              TEXT NOT NULL,
    classification    TEXT,
    authority_scope   TEXT,
    title_hint        TEXT,
    UNIQUE (type, path)
);

CREATE TABLE source_revisions (
    id                      TEXT PRIMARY KEY,
    logical_source_id       TEXT NOT NULL REFERENCES logical_sources(id),
    first_seen_commit_sha   TEXT NOT NULL,
    blob_sha                TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    temporal_scope          TEXT NOT NULL DEFAULT 'current'
        CHECK (temporal_scope IN ('current', 'historical', 'draft', 'proposal', 'future')),
    first_seen_run_id       TEXT NOT NULL REFERENCES scan_runs(id),
    size_bytes              INTEGER,
    UNIQUE (logical_source_id, blob_sha)
);

CREATE TABLE source_revision_occurrences (
    source_revision_id  TEXT NOT NULL REFERENCES source_revisions(id),
    scan_run_id         TEXT NOT NULL REFERENCES scan_runs(id),
    observed_at         TEXT NOT NULL,
    PRIMARY KEY (source_revision_id, scan_run_id)
);

CREATE TABLE authority_rules (
    id                  TEXT PRIMARY KEY,
    resolver_version    TEXT NOT NULL,
    claim_type          TEXT NOT NULL,
    subsystem           TEXT NOT NULL DEFAULT '',
    source_class        TEXT NOT NULL
        CHECK (source_class IN (
            'code', 'wired_path', 'test', 'canonical_doc',
            'progress', 'historical', 'draft', 'proposal', 'future'
        )),
    logical_source_id   TEXT REFERENCES logical_sources(id),
    source_path_pattern TEXT,
    priority            INTEGER NOT NULL
);

-- NULL-safe uniqueness (NULL logical_source_id / pattern collapse to '')
CREATE UNIQUE INDEX uq_authority_rules_nullsafe ON authority_rules (
    resolver_version,
    claim_type,
    subsystem,
    source_class,
    ifnull(logical_source_id, ''),
    ifnull(source_path_pattern, '')
);

-- Entities before candidates that reference them
CREATE TABLE entities (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    stable_label      TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL REFERENCES scan_runs(id),
    created_at        TEXT NOT NULL
);

CREATE TABLE entity_occurrences (
    entity_id   TEXT NOT NULL REFERENCES entities(id),
    scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
    observed_at TEXT NOT NULL,
    PRIMARY KEY (entity_id, scan_run_id)
);

CREATE TABLE edges (
    id                TEXT PRIMARY KEY,
    source_entity_id  TEXT NOT NULL REFERENCES entities(id),
    target_entity_id  TEXT NOT NULL REFERENCES entities(id),
    relation          TEXT NOT NULL,
    first_seen_run_id TEXT NOT NULL REFERENCES scan_runs(id),
    created_at        TEXT NOT NULL,
    UNIQUE (source_entity_id, target_entity_id, relation)
);

CREATE TABLE edge_occurrences (
    edge_id     TEXT NOT NULL REFERENCES edges(id),
    scan_run_id TEXT NOT NULL REFERENCES scan_runs(id),
    observed_at TEXT NOT NULL,
    PRIMARY KEY (edge_id, scan_run_id)
);

CREATE TABLE extraction_candidates (
    id                    TEXT PRIMARY KEY,
    scan_run_id           TEXT NOT NULL REFERENCES scan_runs(id),
    source_revision_id    TEXT NOT NULL REFERENCES source_revisions(id),
    extractor_id          TEXT NOT NULL,
    extractor_version     TEXT NOT NULL,
    candidate_type        TEXT NOT NULL,
    raw_title             TEXT NOT NULL,
    normalized_value_hash TEXT NOT NULL,
    raw_payload           JSON CHECK (raw_payload IS NULL OR json_valid(raw_payload)),
    line_start            INTEGER NOT NULL,
    line_end              INTEGER NOT NULL,
    heading_path          JSON CHECK (heading_path IS NULL OR json_valid(heading_path)),
    excerpt               TEXT,
    confidence            REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    lifecycle_status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (lifecycle_status IN ('pending', 'promoted', 'rejected', 'superseded')),
    promoted_entity_id    TEXT REFERENCES entities(id),
    decided_at            TEXT,
    decided_by            TEXT,
    decision_rationale    TEXT,
    CHECK (line_start >= 1 AND line_start <= line_end),
    UNIQUE (
        source_revision_id, line_start, line_end,
        extractor_id, extractor_version, candidate_type, normalized_value_hash
    )
);

CREATE TABLE edge_candidates (
    id                      TEXT PRIMARY KEY,
    scan_run_id             TEXT NOT NULL REFERENCES scan_runs(id),
    source_revision_id      TEXT NOT NULL REFERENCES source_revisions(id),
    extractor_id            TEXT NOT NULL,
    extractor_version       TEXT NOT NULL,
    source_candidate_id     TEXT REFERENCES extraction_candidates(id),
    target_candidate_id     TEXT REFERENCES extraction_candidates(id),
    source_entity_id        TEXT REFERENCES entities(id),
    target_entity_id        TEXT REFERENCES entities(id),
    relation                TEXT NOT NULL,
    normalized_value_hash   TEXT NOT NULL,
    line_start              INTEGER NOT NULL,
    line_end                INTEGER NOT NULL,
    heading_path            JSON CHECK (heading_path IS NULL OR json_valid(heading_path)),
    excerpt                 TEXT,
    confidence              REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    lifecycle_status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (lifecycle_status IN ('pending', 'promoted', 'rejected', 'superseded')),
    promoted_edge_id        TEXT REFERENCES edges(id),
    decided_at              TEXT,
    decided_by              TEXT,
    decision_rationale      TEXT,
    CHECK (line_start >= 1 AND line_start <= line_end),
    CHECK (
        (source_candidate_id IS NOT NULL AND source_entity_id IS NULL)
        OR
        (source_candidate_id IS NULL AND source_entity_id IS NOT NULL)
    ),
    CHECK (
        (target_candidate_id IS NOT NULL AND target_entity_id IS NULL)
        OR
        (target_candidate_id IS NULL AND target_entity_id IS NOT NULL)
    ),
    UNIQUE (
        source_revision_id, line_start, line_end,
        extractor_id, extractor_version, relation, normalized_value_hash
    )
);

CREATE TABLE observations (
    id                    TEXT PRIMARY KEY,
    entity_id             TEXT NOT NULL REFERENCES entities(id),
    source_revision_id    TEXT NOT NULL REFERENCES source_revisions(id),
    scan_run_id           TEXT NOT NULL REFERENCES scan_runs(id),
    extractor_id          TEXT NOT NULL,
    extractor_version     TEXT NOT NULL,
    normalized_value_hash TEXT NOT NULL,
    line_start            INTEGER NOT NULL,
    line_end              INTEGER NOT NULL,
    heading_path          JSON CHECK (heading_path IS NULL OR json_valid(heading_path)),
    excerpt               TEXT,
    confidence            REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    role                  TEXT,
    from_candidate_id     TEXT REFERENCES extraction_candidates(id),
    CHECK (line_start >= 1 AND line_start <= line_end),
    UNIQUE (
        entity_id, source_revision_id, line_start, line_end,
        extractor_id, extractor_version, normalized_value_hash
    )
);

CREATE TABLE observation_occurrences (
    observation_id TEXT NOT NULL REFERENCES observations(id),
    scan_run_id    TEXT NOT NULL REFERENCES scan_runs(id),
    observed_at    TEXT NOT NULL,
    PRIMARY KEY (observation_id, scan_run_id)
);

CREATE TABLE entity_claims (
    id                    TEXT PRIMARY KEY,
    entity_id             TEXT NOT NULL REFERENCES entities(id),
    claim_type            TEXT NOT NULL,
    claim_value           JSON NOT NULL CHECK (json_valid(claim_value)),
    normalized_value_hash TEXT NOT NULL,
    subsystem             TEXT NOT NULL DEFAULT '',
    temporal_scope        TEXT NOT NULL DEFAULT 'current'
        CHECK (temporal_scope IN ('current', 'historical', 'draft', 'proposal', 'future')),
    source_class          TEXT NOT NULL,
    source_revision_id    TEXT NOT NULL REFERENCES source_revisions(id),
    observation_id        TEXT REFERENCES observations(id),
    scan_run_id           TEXT NOT NULL REFERENCES scan_runs(id),
    extractor_id          TEXT NOT NULL,
    extractor_version     TEXT NOT NULL,
    line_start            INTEGER NOT NULL,
    line_end              INTEGER NOT NULL,
    heading_path          JSON CHECK (heading_path IS NULL OR json_valid(heading_path)),
    excerpt               TEXT,
    confidence            REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    asserted_at           TEXT NOT NULL,
    CHECK (line_start >= 1 AND line_start <= line_end),
    UNIQUE (
        entity_id, claim_type, subsystem,
        source_revision_id, line_start, line_end,
        extractor_id, extractor_version, normalized_value_hash
    )
);

-- resolved value is NOT stored here: read claim_value via JOIN on winning_claim_id
CREATE TABLE entity_resolved (
    entity_id        TEXT NOT NULL REFERENCES entities(id),
    claim_type       TEXT NOT NULL,
    subsystem        TEXT NOT NULL DEFAULT '',
    winning_claim_id TEXT NOT NULL REFERENCES entity_claims(id),
    resolution_rule  TEXT NOT NULL,
    resolver_version TEXT NOT NULL,
    resolved_at      TEXT NOT NULL,
    scan_run_id      TEXT NOT NULL REFERENCES scan_runs(id),
    PRIMARY KEY (entity_id, claim_type, subsystem)
);

CREATE TRIGGER trg_entity_resolved_winning_claim_consistency
BEFORE INSERT ON entity_resolved
BEGIN
    SELECT RAISE(ABORT, 'entity_resolved.winning_claim_id inconsistent with entity/claim_type/subsystem')
    WHERE NOT EXISTS (
        SELECT 1 FROM entity_claims c
        WHERE c.id = NEW.winning_claim_id
          AND c.entity_id = NEW.entity_id
          AND c.claim_type = NEW.claim_type
          AND c.subsystem = NEW.subsystem
    );
END;

CREATE TRIGGER trg_entity_resolved_winning_claim_consistency_upd
BEFORE UPDATE ON entity_resolved
BEGIN
    SELECT RAISE(ABORT, 'entity_resolved.winning_claim_id inconsistent with entity/claim_type/subsystem')
    WHERE NOT EXISTS (
        SELECT 1 FROM entity_claims c
        WHERE c.id = NEW.winning_claim_id
          AND c.entity_id = NEW.entity_id
          AND c.claim_type = NEW.claim_type
          AND c.subsystem = NEW.subsystem
    );
END;

CREATE TABLE edge_observations (
    id                    TEXT PRIMARY KEY,
    edge_id               TEXT NOT NULL REFERENCES edges(id),
    source_revision_id    TEXT NOT NULL REFERENCES source_revisions(id),
    scan_run_id           TEXT NOT NULL REFERENCES scan_runs(id),
    extractor_id          TEXT NOT NULL,
    extractor_version     TEXT NOT NULL,
    normalized_value_hash TEXT NOT NULL,
    line_start            INTEGER NOT NULL,
    line_end              INTEGER NOT NULL,
    heading_path          JSON CHECK (heading_path IS NULL OR json_valid(heading_path)),
    excerpt               TEXT,
    confidence            REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    from_candidate_id     TEXT REFERENCES edge_candidates(id),
    CHECK (line_start >= 1 AND line_start <= line_end),
    UNIQUE (
        edge_id, source_revision_id, line_start, line_end,
        extractor_id, extractor_version, normalized_value_hash
    )
);

CREATE TABLE proposals (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    payload     JSON NOT NULL CHECK (json_valid(payload)),
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    rationale   TEXT,
    scan_run_id TEXT REFERENCES scan_runs(id)
);

CREATE TABLE proposal_status_history (
    id          TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    status      TEXT NOT NULL
        CHECK (status IN ('pending', 'accepted', 'rejected', 'applied_manually', 'superseded')),
    changed_at  TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    note        TEXT
);

CREATE TABLE local_write_tokens (
    token_hash TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    label      TEXT
);

-- Append-only enforcement (DB-level)
CREATE TRIGGER trg_entity_claims_no_update
BEFORE UPDATE ON entity_claims
BEGIN SELECT RAISE(ABORT, 'entity_claims is append-only'); END;

CREATE TRIGGER trg_entity_claims_no_delete
BEFORE DELETE ON entity_claims
BEGIN SELECT RAISE(ABORT, 'entity_claims is append-only'); END;

CREATE TRIGGER trg_proposals_no_update
BEFORE UPDATE ON proposals
BEGIN SELECT RAISE(ABORT, 'proposals is append-only'); END;

CREATE TRIGGER trg_proposals_no_delete
BEFORE DELETE ON proposals
BEGIN SELECT RAISE(ABORT, 'proposals is append-only'); END;

CREATE TRIGGER trg_proposal_status_history_no_update
BEFORE UPDATE ON proposal_status_history
BEGIN SELECT RAISE(ABORT, 'proposal_status_history is append-only'); END;

CREATE TRIGGER trg_proposal_status_history_no_delete
BEFORE DELETE ON proposal_status_history
BEGIN SELECT RAISE(ABORT, 'proposal_status_history is append-only'); END;

CREATE TRIGGER trg_observations_no_update
BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;

CREATE TRIGGER trg_observations_no_delete
BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;

CREATE TRIGGER trg_edge_observations_no_update
BEFORE UPDATE ON edge_observations
BEGIN SELECT RAISE(ABORT, 'edge_observations is append-only'); END;

CREATE TRIGGER trg_edge_observations_no_delete
BEFORE DELETE ON edge_observations
BEGIN SELECT RAISE(ABORT, 'edge_observations is append-only'); END;

CREATE TRIGGER trg_source_revisions_no_update
BEFORE UPDATE ON source_revisions
BEGIN SELECT RAISE(ABORT, 'source_revisions is append-only'); END;

CREATE TRIGGER trg_source_revisions_no_delete
BEFORE DELETE ON source_revisions
BEGIN SELECT RAISE(ABORT, 'source_revisions is append-only'); END;

CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entity_claims_entity ON entity_claims(entity_id);
CREATE INDEX idx_entity_claims_type ON entity_claims(claim_type);
CREATE INDEX idx_observations_entity ON observations(entity_id);
CREATE INDEX idx_observations_revision ON observations(source_revision_id);
CREATE INDEX idx_edges_source ON edges(source_entity_id);
CREATE INDEX idx_edges_target ON edges(target_entity_id);
CREATE INDEX idx_extraction_candidates_status ON extraction_candidates(lifecycle_status);
CREATE INDEX idx_edge_candidates_status ON edge_candidates(lifecycle_status);
CREATE INDEX idx_source_revisions_logical ON source_revisions(logical_source_id);
CREATE INDEX idx_scan_runs_commit ON scan_runs(repository_commit_sha);
CREATE INDEX idx_authority_rules_lookup ON authority_rules(resolver_version, claim_type, subsystem);

INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1');
INSERT INTO schema_meta (key, value) VALUES ('resolver_version', '1');