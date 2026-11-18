PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    uid             INTEGER PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    avatar_url      TEXT NOT NULL DEFAULT '',
    image_dir       TEXT NOT NULL DEFAULT '',
    last_dynamic_id TEXT NOT NULL DEFAULT '0',
    last_sync_at    INTEGER,
    enabled         INTEGER NOT NULL DEFAULT 1,
    avatar_file     TEXT NOT NULL DEFAULT '',
    avatar_source   TEXT NOT NULL DEFAULT '',
    avatar_updated_at INTEGER,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL DEFAULT 0,
    updated_at      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS posts (
    id             INTEGER PRIMARY KEY,
    dynamic_id     TEXT NOT NULL UNIQUE,
    uid            INTEGER NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
    dynamic_type   TEXT NOT NULL DEFAULT '',
    major_type     TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT 'other',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    published_at   INTEGER NOT NULL DEFAULT 0,
    collected_at   INTEGER NOT NULL DEFAULT 0,
    is_charge      INTEGER NOT NULL DEFAULT 0,
    is_accessible  INTEGER NOT NULL DEFAULT 1,
    image_count    INTEGER NOT NULL DEFAULT 0,
    search_text    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_posts_uid_time ON posts(uid, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_kind ON posts(kind);

CREATE TABLE IF NOT EXISTS images (
    id             INTEGER PRIMARY KEY,
    post_id        INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    url_clean      TEXT NOT NULL DEFAULT '',
    file_name      TEXT NOT NULL,
    width          INTEGER NOT NULL DEFAULT 0,
    height         INTEGER NOT NULL DEFAULT 0,
    remote_size    INTEGER,
    local_size     INTEGER,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    http_status    INTEGER,
    last_error     TEXT,
    downloaded_at  INTEGER,
    UNIQUE(post_id, url)
);

CREATE INDEX IF NOT EXISTS idx_images_post ON images(post_id);
CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);

CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY,
    uid          INTEGER NOT NULL,
    started_at   INTEGER NOT NULL DEFAULT 0,
    finished_at  INTEGER,
    status       TEXT NOT NULL DEFAULT 'running',
    pages        INTEGER NOT NULL DEFAULT 0,
    new_posts    INTEGER NOT NULL DEFAULT 0,
    new_images   INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_uid ON sync_runs(uid, started_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    created_at  INTEGER NOT NULL DEFAULT 0,
    started_at  INTEGER,
    finished_at INTEGER,
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    log_path    TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

PRAGMA user_version = 4;
