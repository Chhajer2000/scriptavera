-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Vaibhav Chhajer
--
-- Scriptavera's tables in Supabase (Postgres). Run once in the Supabase SQL editor (it is safe to run again).
--
-- Every table has row level security turned ON and NO policy: nothing can be read or written with the public
-- "anon" key. Only the server, holding the service_role key (SUPABASE_KEY on Cloud Run), reaches these tables.
-- The same tables exist in SQLite for development (scriptavera/accounts.py, SQLITE_SCHEMA).

create extension if not exists pg_trgm;

-- ------------------------------------------------------------------ people
create table if not exists public.users (
  id                     uuid primary key default gen_random_uuid(),
  google_sub             text not null unique,              -- Google's permanent id for the account
  email                  text not null,
  email_verified         boolean not null default false,
  full_name              text,
  given_name             text,
  family_name            text,
  picture_url            text,
  locale                 text,
  hosted_domain          text,                              -- e.g. the university, for Google Workspace accounts
  orcid                  text check (orcid is null or orcid ~ '^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$'),   -- optional
  affiliation            text,
  department             text,
  country                text,
  homepage               text,
  drive_connected        boolean not null default false,    -- has given drive.file permission (needed for signatures)
  drive_connected_at     timestamptz,
  drive_email            text,
  free_papers_used       integer not null default 0 check (free_papers_used >= 0),
  papers_total           integer not null default 0,
  dataset_opt_in_default boolean not null default false,
  terms_accepted_at      timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  last_login_at          timestamptz
);
create index if not exists users_email on public.users (lower(email));

create table if not exists public.subscriptions (
  user_id                  uuid primary key references public.users(id) on delete cascade,
  provider                 text,                            -- 'razorpay'
  provider_subscription_id text unique,
  plan                     text,
  status                   text check (status in ('created','authenticated','active','pending','halted',
                                                  'paused','cancelled','completed','expired')),
  current_period_end       timestamptz,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

-- ------------------------------------------------------------------ the free plan for guests
-- One row per network. The address is stored only as a keyed hash (HMAC-SHA256 with the server's secret);
-- IPv6 addresses are counted per /64.
create table if not exists public.guest_ip_usage (
  ip_hash     text primary key,
  papers_used integer not null default 0 check (papers_used >= 0),
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now()
);

-- ------------------------------------------------------------------ papers
create table if not exists public.papers (
  id               text primary key,                        -- Scriptavera's project id
  owner_user_id    uuid references public.users(id) on delete set null,
  guest_id         text,                                    -- the browser that made it, before signing in
  ip_hash          text,
  name             text,
  title            text,
  domains          jsonb not null default '[]',
  authors          jsonb not null default '[]',             -- as entered: name, affiliation, email, orcid
  dataset_opt_in   boolean not null default false,          -- the author ticked "Help improve Scriptavera"
  dataset_id       text,                                    -- the anonymous folder in the test-suite bucket
  dataset_saved_at timestamptz,
  signed           boolean not null default false,          -- a signed, RFC 3161 timestamped record was made
  status           text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  completed_at     timestamptz,
  downloaded_at    timestamptz,
  purge_after      timestamptz,
  purged_at        timestamptz,
  drive_file_id    text,
  deleted_at       timestamptz
);
alter table public.papers add column if not exists dataset_consent_at timestamptz;     -- when the box was ticked
alter table public.papers add column if not exists dataset_consent_version text;      -- which wording was agreed to
create index if not exists papers_owner on public.papers (owner_user_id);
create index if not exists papers_guest on public.papers (guest_id) where owner_user_id is null;

-- ------------------------------------------------------------------ collaborators
-- Everyone a user has written a paper with. Filled by remember_collaborators() whenever a paper is made, so the
-- list grows by itself and the next paper can offer these people again (name, e-mail, affiliation, ORCID).
create table if not exists public.collaborators (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references public.users(id) on delete cascade,
  full_name          text not null,
  email              text,
  affiliation        text,
  orcid              text,
  times_collaborated integer not null default 0,
  first_paper_id     text,
  last_paper_id      text,
  first_seen         timestamptz not null default now(),
  last_seen          timestamptz not null default now()
);
create index if not exists collaborators_user on public.collaborators (user_id);
create unique index if not exists collaborators_user_orcid on public.collaborators (user_id, orcid) where orcid is not null;
create index if not exists collaborators_name_trgm on public.collaborators using gin (lower(full_name) gin_trgm_ops);

create table if not exists public.paper_collaborators (
  paper_id        text not null references public.papers(id) on delete cascade,
  collaborator_id uuid not null references public.collaborators(id) on delete cascade,
  position        integer,
  primary key (paper_id, collaborator_id)
);

-- ------------------------------------------------------------------ what Scriptavera learns about each person
-- The meanings a person confirmed for their symbols ('notation') and the readings of their handwriting they
-- corrected ('handwriting'). One row per person and kind; used only for that person's own later papers.
create table if not exists public.user_memory (
  user_id    uuid not null references public.users(id) on delete cascade,
  kind       text not null check (kind in ('notation', 'handwriting')),
  data       jsonb not null default '{}',
  updated_at timestamptz not null default now(),
  primary key (user_id, kind)
);

-- ------------------------------------------------------------------ audit ledger of signatures
-- An index of what is kept in the Cloud Storage bucket under signatures/<paper id>/.
create table if not exists public.signatures (
  paper_id   text not null,
  kind       text not null,                                 -- 'notes' or 'version-NNN'
  digest     text not null,                                 -- SHA-256 of the signed record
  tsa_url    text,
  tsa_time   timestamptz,
  gcs_prefix text,
  created_at timestamptz not null default now(),
  primary key (paper_id, digest)
);

create table if not exists public.billing_events (
  id          text primary key,                             -- the provider's event id: each is applied once
  provider    text,
  user_id     uuid,
  type        text,
  payload     jsonb,
  received_at timestamptz not null default now()
);

-- ------------------------------------------------------------------ row level security: server only
alter table public.users               enable row level security;
alter table public.subscriptions       enable row level security;
alter table public.guest_ip_usage      enable row level security;
alter table public.papers              enable row level security;
alter table public.collaborators       enable row level security;
alter table public.paper_collaborators enable row level security;
alter table public.signatures          enable row level security;
alter table public.billing_events      enable row level security;
alter table public.user_memory         enable row level security;

-- ------------------------------------------------------------------ updated_at
create or replace function public.touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
drop trigger if exists users_touch on public.users;
create trigger users_touch before update on public.users for each row execute function public.touch_updated_at();
drop trigger if exists papers_touch on public.papers;
create trigger papers_touch before update on public.papers for each row execute function public.touch_updated_at();
drop trigger if exists subscriptions_touch on public.subscriptions;
create trigger subscriptions_touch before update on public.subscriptions for each row execute function public.touch_updated_at();

-- ------------------------------------------------------------------ atomic counters for the free plan
drop function if exists public.claim_guest_paper(text, integer);       -- the first version, without the window
create or replace function public.claim_guest_paper(p_ip_hash text, p_limit integer, p_window_days integer default 30)
returns table (allowed boolean, used integer) language plpgsql security definer set search_path = public as $$
declare v integer;
begin
  insert into guest_ip_usage (ip_hash, papers_used) values (p_ip_hash, 0) on conflict (ip_hash) do nothing;
  -- a network's count starts again after the window (a university's guests are counted per month, not for ever)
  update guest_ip_usage set papers_used = 0, first_seen = now()
    where ip_hash = p_ip_hash and first_seen < now() - make_interval(days => p_window_days);
  select papers_used into v from guest_ip_usage where ip_hash = p_ip_hash for update;
  if v >= p_limit then return query select false, v; return; end if;
  update guest_ip_usage set papers_used = papers_used + 1, last_seen = now() where ip_hash = p_ip_hash;
  return query select true, v + 1;
end $$;

create or replace function public.release_guest_paper(p_ip_hash text) returns void
language sql security definer set search_path = public as $$
  update guest_ip_usage set papers_used = greatest(papers_used - 1, 0) where ip_hash = p_ip_hash;
$$;

create or replace function public.claim_user_paper(p_user uuid, p_limit integer)
returns table (allowed boolean, used integer) language plpgsql security definer set search_path = public as $$
declare v integer;
begin
  select free_papers_used into v from users where id = p_user for update;
  if v is null then return query select false, 0; return; end if;
  if v >= p_limit then return query select false, v; return; end if;
  update users set free_papers_used = free_papers_used + 1 where id = p_user;
  return query select true, v + 1;
end $$;

create or replace function public.release_user_paper(p_user uuid) returns void
language sql security definer set search_path = public as $$
  update users set free_papers_used = greatest(free_papers_used - 1, 0) where id = p_user;
$$;

-- Papers made as a guest in this browser become the account's when the person signs in, and count towards the
-- account's free papers (three in all, before or after signing in).
create or replace function public.adopt_guest_papers(p_guest text, p_user uuid) returns integer
language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  update papers set owner_user_id = p_user where guest_id = p_guest and owner_user_id is null and deleted_at is null;
  get diagnostics n = row_count;
  if n > 0 then
    update users set free_papers_used = free_papers_used + n, papers_total = papers_total + n where id = p_user;
  end if;
  return n;
end $$;

create or replace function public.count_paper() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.owner_user_id is not null then update users set papers_total = papers_total + 1 where id = new.owner_user_id; end if;
  return new;
end $$;
drop trigger if exists papers_count on public.papers;
create trigger papers_count after insert on public.papers for each row execute function public.count_paper();

-- ------------------------------------------------------------------ collaborators, kept up to date
-- p_authors: [{name, email, affiliation, orcid}, ...]. The user themself (p_self_email) is skipped. A person is
-- recognised by ORCID or e-mail first, then by name when neither record says otherwise; their details are
-- completed with what is new. Calling it twice for one paper counts the paper once.
create or replace function public.remember_collaborators(p_user uuid, p_paper text, p_authors jsonb, p_self_email text)
returns integer language plpgsql security definer set search_path = public as $$
declare a jsonb; v_name text; v_email text; v_aff text; v_orcid text; cid uuid; pos integer := 0; n integer := 0; linked integer;
begin
  for a in select * from jsonb_array_elements(coalesce(p_authors, '[]'::jsonb)) loop
    pos := pos + 1;
    v_name := nullif(btrim(a->>'name'), '');
    v_email := nullif(lower(btrim(a->>'email')), '');
    v_aff := nullif(btrim(a->>'affiliation'), '');
    v_orcid := nullif(upper(btrim(a->>'orcid')), '');
    continue when v_name is null;
    continue when p_self_email <> '' and v_email = lower(p_self_email);
    cid := null;
    if v_orcid is not null or v_email is not null then
      select id into cid from collaborators where user_id = p_user
        and ((v_orcid is not null and orcid = v_orcid) or (v_email is not null and lower(email) = v_email)) limit 1;
    end if;
    if cid is null then
      select id into cid from collaborators where user_id = p_user and lower(full_name) = lower(v_name)
        and (orcid is null or v_orcid is null or orcid = v_orcid)
        and (email is null or v_email is null or lower(email) = v_email) limit 1;
    end if;
    if cid is null then
      insert into collaborators (user_id, full_name, email, affiliation, orcid, first_paper_id, last_paper_id)
        values (p_user, v_name, v_email, v_aff, v_orcid, p_paper, p_paper) returning id into cid;
    else
      update collaborators set full_name = v_name, email = coalesce(v_email, email), affiliation = coalesce(v_aff, affiliation),
        orcid = coalesce(v_orcid, orcid), last_paper_id = p_paper, last_seen = now() where id = cid;
    end if;
    insert into paper_collaborators (paper_id, collaborator_id, position) values (p_paper, cid, pos - 1)
      on conflict do nothing;
    get diagnostics linked = row_count;
    if linked > 0 then
      update collaborators set times_collaborated = times_collaborated + 1 where id = cid;
      n := n + 1;
    end if;
  end loop;
  return n;
end $$;

-- The collaborators offered while typing an author's name: people written with most often first.
create or replace function public.search_collaborators(p_user uuid, p_query text, p_limit integer default 8)
returns table (id uuid, full_name text, email text, affiliation text, orcid text, times_collaborated integer, last_seen timestamptz)
language sql stable security definer set search_path = public as $$
  select c.id, c.full_name, c.email, c.affiliation, c.orcid, c.times_collaborated, c.last_seen
  from collaborators c
  where c.user_id = p_user and (coalesce(p_query, '') = '' or lower(c.full_name) like '%' || lower(p_query) || '%'
        or lower(coalesce(c.email, '')) like '%' || lower(p_query) || '%' or coalesce(c.orcid, '') like '%' || p_query || '%'
        or similarity(lower(c.full_name), lower(p_query)) > 0.3)
  order by (lower(c.full_name) like lower(p_query) || '%') desc, c.times_collaborated desc, c.last_seen desc
  limit greatest(1, least(p_limit, 25));
$$;

-- Only the server may call these.
revoke all on function public.claim_guest_paper(text, integer, integer), public.release_guest_paper(text),
  public.claim_user_paper(uuid, integer), public.release_user_paper(uuid), public.adopt_guest_papers(text, uuid),
  public.remember_collaborators(uuid, text, jsonb, text), public.search_collaborators(uuid, text, integer)
  from public, anon, authenticated;
grant execute on function public.claim_guest_paper(text, integer, integer), public.release_guest_paper(text),
  public.claim_user_paper(uuid, integer), public.release_user_paper(uuid), public.adopt_guest_papers(text, uuid),
  public.remember_collaborators(uuid, text, jsonb, text), public.search_collaborators(uuid, text, integer)
  to service_role;

-- One view for the team: who uses Scriptavera, how much, and whether they pay.
create or replace view public.user_overview with (security_invoker = true) as
  select u.id, u.email, u.full_name, u.orcid, u.affiliation, u.drive_connected, u.free_papers_used, u.papers_total,
         s.status as subscription_status, s.current_period_end, u.created_at, u.last_login_at,
         (select count(*) from collaborators c where c.user_id = u.id) as collaborators
  from users u left join subscriptions s on s.user_id = u.id;

-- Ensure service_role has full table and sequence access for backend operations
grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant all on all routines in schema public to service_role;
alter default privileges in schema public grant all on tables to service_role;
alter default privileges in schema public grant all on sequences to service_role;
alter default privileges in schema public grant all on routines to service_role;

