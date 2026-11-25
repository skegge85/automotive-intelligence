-- Apply schema.sql first. Insert the single operator's auth UUID as described in README.
create table if not exists public.operators (
  user_id uuid primary key references auth.users(id) on delete cascade
);
create table if not exists public.pipeline (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  vacancy_id text not null references public.vacancies(id) on delete cascade,
  bookmarked boolean not null default false,
  application_status text not null default 'watching'
    check (application_status in ('watching','applied','interview','offer','closed')),
  notes text not null default '' check (char_length(notes) <= 12000),
  primary key (user_id, vacancy_id)
);
alter table public.operators enable row level security;
alter table public.pipeline enable row level security;
create policy own_operator on public.operators for select to authenticated using (user_id = auth.uid());
create policy own_pipeline on public.pipeline for all to authenticated
  using (user_id = auth.uid() and exists (select 1 from public.operators where user_id=auth.uid()))
  with check (user_id = auth.uid() and exists (select 1 from public.operators where user_id=auth.uid()));
revoke all on public.operators, public.pipeline from public, anon, authenticated;
grant select on public.operators to authenticated;
grant select, insert, update on public.pipeline to authenticated;
do $$
declare tbl text;
begin
  foreach tbl in array array['employers','runs','checks','vacancies','vacancy_events','deliveries'] loop
    execute format('alter table public.%I enable row level security', tbl);
    execute format('revoke all on public.%I from public, anon, authenticated', tbl);
    if tbl <> 'deliveries' then
      execute format('grant select on public.%I to authenticated', tbl);
      execute format('create policy operator_read on public.%I for select to authenticated using (exists (select 1 from public.operators where user_id=auth.uid()))', tbl);
    end if;
  end loop;
end $$;
