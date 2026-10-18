-- Tiger Data (Postgres + pgvector) schema. Safe to run more than once.
-- Two halves: the UK law knowledge base (written by scripts/ingest_law.py) and
-- users + cases (one case per tenancy, shared by its tenants).

CREATE EXTENSION IF NOT EXISTS vector;

-- ===== Law knowledge base =====

CREATE TABLE IF NOT EXISTS law_sources (
  id            serial PRIMARY KEY,
  name          text NOT NULL UNIQUE,      -- 'Tenant Fees Act 2019'
  url           text,                      -- legislation.gov.uk / gov.uk page
  last_updated  date,                      -- as shown on legislation.gov.uk
  ingested_at   timestamptz NOT NULL DEFAULT now()
);

-- A long section is split into several rows with the same section_ref.
CREATE TABLE IF NOT EXISTS law_sections (
  id           bigserial PRIMARY KEY,
  source_id    int NOT NULL REFERENCES law_sources(id) ON DELETE CASCADE,
  section_ref  text NOT NULL,              -- 's.1', 'Sch 1 para 2'
  heading      text,
  text         text NOT NULL,
  url          text,                       -- deep link to the section
  embedding    vector(768) NOT NULL        -- Gemini embedding, 768 dims so HNSW can index it
);
CREATE INDEX IF NOT EXISTS law_sections_embedding_idx
  ON law_sections USING hnsw (embedding vector_cosine_ops);

-- ===== Users and cases =====

-- Login: email + password for the MVP; google_id is for Google sign-in later.
CREATE TABLE IF NOT EXISTS users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email          text NOT NULL UNIQUE,
  display_name   text NOT NULL,
  password_hash  text,                     -- bcrypt/argon2, never the password itself
  google_id      text UNIQUE,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (password_hash IS NOT NULL OR google_id IS NOT NULL)
);

-- One case per tenancy. The code links the website and the phone app, and is
-- how co-tenants join.
CREATE TABLE IF NOT EXISTS cases (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code              text NOT NULL UNIQUE,  -- 'FLAT-7KQ2'
  property_address  text NOT NULL,
  postcode          text,
  tenancy_start     date,
  tenancy_end       date,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- Case to users is many to many. A tenant is a name on the agreement; user_id is
-- filled in when that person signs up and enters the case code.
CREATE TABLE IF NOT EXISTS tenants (
  id       bigserial PRIMARY KEY,
  case_id  uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
  name     text NOT NULL,
  role     text NOT NULL CHECK (role IN ('lead', 'co_tenant')),
  UNIQUE (case_id, user_id)
);

-- One contract per case; re-uploading replaces it.
CREATE TABLE IF NOT EXISTS agreements (
  id                 bigserial PRIMARY KEY,
  case_id            uuid NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
  original_filename  text,
  object_key         text,                 -- PDF in Vultr Object Storage
  page_count         int,
  text               text NOT NULL,
  summary            text,                 -- one plain-language paragraph about the whole agreement
  uploaded_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clauses (
  id            bigserial PRIMARY KEY,
  agreement_id  bigint NOT NULL REFERENCES agreements(id) ON DELETE CASCADE,
  number        text NOT NULL,             -- '4.2' as printed
  text          text NOT NULL
);

-- cited_source / cited_section keep the citation if the law is re-ingested.
CREATE TABLE IF NOT EXISTS flags (
  id              bigserial PRIMARY KEY,
  clause_id       bigint NOT NULL REFERENCES clauses(id) ON DELETE CASCADE,
  kind            text NOT NULL CHECK (kind IN ('need_to_know', 'clarify', 'contradicts_law')),
  topic           text,                    -- e.g. 'Guests', 'Pets'; only set for need-to-knows
  explanation     text NOT NULL,
  law_section_id  bigint REFERENCES law_sections(id) ON DELETE SET NULL,
  cited_source    text,
  cited_section   text,
  status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Latest landlord email draft only.
CREATE TABLE IF NOT EXISTS emails (
  id          bigserial PRIMARY KEY,
  case_id     uuid NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
  body        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Move-in photos are evidence only; no AI analysis.
CREATE TABLE IF NOT EXISTS photos (
  id           bigserial PRIMARY KEY,
  case_id      uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  room         text NOT NULL,              -- 'kitchen', 'bedroom 1'
  object_key   text NOT NULL,              -- file in Vultr Object Storage
  taken_at     timestamptz,                -- from EXIF if present
  uploaded_at  timestamptz NOT NULL DEFAULT now(),
  note         text
);

-- Completed analysis results, retained independently of user accounts/case setup.
CREATE TABLE IF NOT EXISTS tenancy_reviews (
  id                text PRIMARY KEY,
  lead_tenant       text NOT NULL,
  co_tenants        jsonb NOT NULL,
  property_address  text NOT NULL,
  filename          text,
  review            jsonb NOT NULL,
  owner_account_id  text,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- FlatMate account-backed tenancy profiles. Shared codes are only issued after
-- the agreement is signed and the tenancy start date has arrived.
CREATE TABLE IF NOT EXISTS flatmate_accounts (
  id            text PRIMARY KEY,
  email         text NOT NULL UNIQUE,
  display_name  text NOT NULL,
  password_hash text NOT NULL,
  created_at    text NOT NULL
);

CREATE TABLE IF NOT EXISTS flatmate_sessions (
  token_hash text PRIMARY KEY,
  account_id text NOT NULL REFERENCES flatmate_accounts(id) ON DELETE CASCADE,
  expires_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_profiles (
  id                text PRIMARY KEY,
  code              text UNIQUE,
  owner_account_id  text NOT NULL REFERENCES flatmate_accounts(id),
  household_mode    text NOT NULL,
  hmo_status        text NOT NULL,
  property_address  text NOT NULL,
  lead_tenant       text NOT NULL,
  co_tenants        text NOT NULL,
  current_tenant    text NOT NULL,
  "current_role"    text NOT NULL,
  agreement_signed  integer NOT NULL DEFAULT 0,
  signed_on         text,
  tenancy_start     text,
  is_active         integer NOT NULL DEFAULT 0,
  review_id         text NOT NULL,
  review            text NOT NULL,
  created_at        text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_members (
  profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  account_id text NOT NULL REFERENCES flatmate_accounts(id) ON DELETE CASCADE,
  name       text NOT NULL,
  role       text NOT NULL CHECK (role IN ('lead', 'co_tenant')),
  is_current integer NOT NULL DEFAULT 1,
  PRIMARY KEY (profile_id, account_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_current_tenancy_per_account
  ON tenancy_members(account_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS tenancy_chat_messages (
  id                text PRIMARY KEY,
  profile_id        text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  account_id        text NOT NULL REFERENCES flatmate_accounts(id),
  user_message      text NOT NULL,
  assistant_message text NOT NULL,
  landlord_draft    text NOT NULL,
  created_at        text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_clause_status (
  profile_id  text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  issue_key   text NOT NULL,
  status      text NOT NULL CHECK (status IN ('open', 'resolved')),
  update_note text NOT NULL,
  updated_by  text NOT NULL REFERENCES flatmate_accounts(id),
  updated_at  text NOT NULL,
  PRIMARY KEY (profile_id, issue_key)
);

CREATE TABLE IF NOT EXISTS tenancy_photos (
  id          text PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  account_id  text NOT NULL REFERENCES flatmate_accounts(id),
  room        text NOT NULL,
  note        text NOT NULL,
  storage_key text NOT NULL,
  media_type  text NOT NULL,
  uploaded_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_calendar_items (
  id          text PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  created_by  text NOT NULL REFERENCES flatmate_accounts(id) ON DELETE CASCADE,
  title       text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('rent', 'bill')),
  due_date    text NOT NULL,
  recurrence  text NOT NULL CHECK (recurrence IN ('once', 'monthly')),
  amount      numeric,
  created_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS flatmate_notification_preferences (
  account_id             text PRIMARY KEY REFERENCES flatmate_accounts(id) ON DELETE CASCADE,
  email_reminders_enabled boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS tenancy_calendar_reminders (
  item_id     text NOT NULL REFERENCES tenancy_calendar_items(id) ON DELETE CASCADE,
  account_id  text NOT NULL REFERENCES flatmate_accounts(id) ON DELETE CASCADE,
  due_date    text NOT NULL,
  status      text NOT NULL,
  updated_at  text NOT NULL,
  PRIMARY KEY (item_id, account_id, due_date)
);
