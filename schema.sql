create table if not exists employers (
  id text primary key, name text not null, rank integer, ranking_year integer, priority integer not null,
  portal text, method text, board text, metadata text not null, last_attempt text, last_success text, status text
);
create table if not exists runs (
  id text primary key, started text not null, finished text, status text not null, audit text
);
create table if not exists checks (
  run_id text references runs(id), employer_id text references employers(id), attempted integer not null,
  started text not null, finished text not null, status text not null, retrieved integer,
  eligible integer, evidence text not null, primary key(run_id, employer_id)
);
create table if not exists vacancies (
  id text primary key, employer_id text not null references employers(id), requisition text,
  url text not null, normalized_url text not null, title text not null, location text,
  status text not null, source_type text not null, evidence text, score integer not null,
  first_seen text, last_verified text, last_changed text not null, details text not null,
  unique(employer_id, normalized_url), unique(employer_id, requisition)
);
create table if not exists vacancy_events (
  id text primary key, vacancy_id text not null references vacancies(id), recorded text not null,
  kind text not null, snapshot text not null
);
create table if not exists deliveries (
  id text primary key, kind text not null, subject text not null, body text not null,
  created text not null, sent text
);
create index if not exists vacancy_status_score on vacancies(status, score);
create index if not exists deliveries_pending on deliveries(sent, created);
