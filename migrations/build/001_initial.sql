-- Extension additive : aucun objet des anciens modules n'est modifié.
CREATE TABLE IF NOT EXISTS evolution_build_schema (version integer PRIMARY KEY);
INSERT INTO evolution_build_schema(version) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS evolution_build_snapshots (
  kind text NOT NULL CHECK(kind IN ('catalog','rules')), id text NOT NULL,
  payload text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(kind,id)
);
CREATE TABLE IF NOT EXISTS evolution_builds (
  id uuid PRIMARY KEY, guild_id bigint NOT NULL, owner_id bigint NOT NULL,
  revision integer NOT NULL CHECK(revision>0), payload jsonb NOT NULL,
  catalog_id text NOT NULL, rules_id text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evolution_builds_owner ON evolution_builds(guild_id,owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS evolution_build_history (
  build_id uuid NOT NULL REFERENCES evolution_builds(id) ON DELETE CASCADE,
  revision integer NOT NULL, payload jsonb NOT NULL, report_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(build_id,revision)
);
CREATE TABLE IF NOT EXISTS evolution_build_operations (
  guild_id bigint NOT NULL, actor_id bigint NOT NULL, operation_id text NOT NULL,
  request_hash text NOT NULL, build_id uuid, result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(guild_id,actor_id,operation_id)
);
CREATE INDEX IF NOT EXISTS evolution_build_operations_created ON evolution_build_operations(created_at);
CREATE TABLE IF NOT EXISTS evolution_build_shares (
  token_hash text PRIMARY KEY, build_id uuid NOT NULL REFERENCES evolution_builds(id) ON DELETE CASCADE,
  guild_id bigint NOT NULL, owner_id bigint NOT NULL, revision integer NOT NULL,
  payload jsonb NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz,
  state text NOT NULL CHECK(state IN ('pending','sending','sent','failed')) DEFAULT 'pending',
  message_id bigint, channel_id bigint
);
CREATE INDEX IF NOT EXISTS evolution_build_shares_owner ON evolution_build_shares(guild_id,owner_id,expires_at);
