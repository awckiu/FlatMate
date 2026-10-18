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
  object_key         text,                 -- unused: the PDF is stored in pdf (below)
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

-- (The old Vultr-storage `photos` table is gone: move-in photos live in tenancy_photos, below.)
DROP TABLE IF EXISTS photos;

-- Saved analyses: what the results page needs to show a case again.
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS analysis_mode text;        -- 'gemini' or 'placeholder'
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS law_data_available boolean;
ALTER TABLE flags ADD COLUMN IF NOT EXISTS excerpt text;                   -- short quote from the clause
ALTER TABLE flags ADD COLUMN IF NOT EXISTS cited_url text;

-- Household setup chosen for the review (solo or friends, and HMO status), so reopening a case shows it.
ALTER TABLE cases ADD COLUMN IF NOT EXISTS household_mode text;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS hmo_status text;

-- The original agreement PDF, so it can be downloaded again later.
ALTER TABLE agreements ADD COLUMN IF NOT EXISTS pdf bytea;

-- FlatMate tenancy profiles. They hang off the same users table as the login (there is no
-- separate accounts table). A shared HOME- code is only issued after the agreement is signed
-- and the tenancy start date has arrived.
CREATE TABLE IF NOT EXISTS tenancy_profiles (
  id                text PRIMARY KEY,
  code              text UNIQUE,
  owner_account_id  uuid NOT NULL REFERENCES users(id),
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
  review_id         text NOT NULL,        -- the FLAT- case code this tenancy was made from
  review            text NOT NULL,
  created_at        text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_members (
  profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
  account_id        uuid NOT NULL REFERENCES users(id),
  user_message      text NOT NULL,
  assistant_message text NOT NULL,
  landlord_draft    text NOT NULL,
  created_at        text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_photos (
  id          text PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  account_id  uuid NOT NULL REFERENCES users(id),
  room        text NOT NULL,
  note        text NOT NULL,
  data        bytea NOT NULL,             -- the image itself (no S3 or disk copy)
  media_type  text NOT NULL,
  uploaded_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS tenancy_calendar_items (
  id          text PRIMARY KEY,
  profile_id  text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
  created_by  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('rent', 'bill')),
  due_date    text NOT NULL,
  recurrence  text NOT NULL CHECK (recurrence IN ('once', 'monthly')),
  amount      numeric,
  created_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS flatmate_notification_preferences (
  account_id              uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  email_reminders_enabled boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS tenancy_calendar_reminders (
  item_id     text NOT NULL REFERENCES tenancy_calendar_items(id) ON DELETE CASCADE,
  account_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  due_date    text NOT NULL,
  status      text NOT NULL,
  updated_at  text NOT NULL,
  PRIMARY KEY (item_id, account_id, due_date)
);

-- ===== One-off upgrades for databases made before the tenancy tables were merged in here =====
-- Each is a no-op on a fresh database.
ALTER TABLE tenancy_profiles ADD COLUMN IF NOT EXISTS review_id text;
ALTER TABLE tenancy_photos ADD COLUMN IF NOT EXISTS data bytea;      -- photos used to be files
ALTER TABLE tenancy_photos DROP COLUMN IF EXISTS storage_key;
ALTER TABLE tenancy_calendar_items ALTER COLUMN amount TYPE numeric;

DO $$
DECLARE
  fk record;
BEGIN
  -- The old flatmate_accounts table mirrored users (same ids), so point everything at users.
  IF to_regclass('flatmate_accounts') IS NOT NULL THEN
    FOR fk IN
      SELECT conrelid::regclass AS tbl, conname FROM pg_constraint
      WHERE contype = 'f' AND confrelid = 'flatmate_accounts'::regclass
    LOOP
      EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', fk.tbl, fk.conname);
    END LOOP;
    ALTER TABLE tenancy_profiles ALTER COLUMN owner_account_id TYPE uuid USING owner_account_id::uuid;
    ALTER TABLE tenancy_members ALTER COLUMN account_id TYPE uuid USING account_id::uuid;
    ALTER TABLE tenancy_chat_messages ALTER COLUMN account_id TYPE uuid USING account_id::uuid;
    ALTER TABLE tenancy_photos ALTER COLUMN account_id TYPE uuid USING account_id::uuid;
    ALTER TABLE tenancy_calendar_items ALTER COLUMN created_by TYPE uuid USING created_by::uuid;
    ALTER TABLE flatmate_notification_preferences ALTER COLUMN account_id TYPE uuid USING account_id::uuid;
    ALTER TABLE tenancy_calendar_reminders ALTER COLUMN account_id TYPE uuid USING account_id::uuid;
    ALTER TABLE tenancy_profiles ADD FOREIGN KEY (owner_account_id) REFERENCES users(id);
    ALTER TABLE tenancy_members ADD FOREIGN KEY (account_id) REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE tenancy_chat_messages ADD FOREIGN KEY (account_id) REFERENCES users(id);
    ALTER TABLE tenancy_photos ADD FOREIGN KEY (account_id) REFERENCES users(id);
    ALTER TABLE tenancy_calendar_items ADD FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE flatmate_notification_preferences ADD FOREIGN KEY (account_id) REFERENCES users(id) ON DELETE CASCADE;
    ALTER TABLE tenancy_calendar_reminders ADD FOREIGN KEY (account_id) REFERENCES users(id) ON DELETE CASCADE;
    DROP TABLE flatmate_accounts;
  END IF;

  -- The CHECKs the old tables never had; NOT VALID applies them to new rows only.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenancy_members_role_check') THEN
    ALTER TABLE tenancy_members ADD CONSTRAINT tenancy_members_role_check
      CHECK (role IN ('lead', 'co_tenant')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenancy_calendar_items_kind_check') THEN
    ALTER TABLE tenancy_calendar_items ADD CONSTRAINT tenancy_calendar_items_kind_check
      CHECK (kind IN ('rent', 'bill')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tenancy_calendar_items_recurrence_check') THEN
    ALTER TABLE tenancy_calendar_items ADD CONSTRAINT tenancy_calendar_items_recurrence_check
      CHECK (recurrence IN ('once', 'monthly')) NOT VALID;
  END IF;
END $$;
