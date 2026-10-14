create table if not exists public.wedding_projects (
    project_id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users (id) on delete cascade,
    project_name text not null check (length(trim(project_name)) between 1 and 120),
    wedding_profile jsonb not null default '{}'::jsonb
        check (jsonb_typeof(wedding_profile) = 'object'),
    selected_ceremonies jsonb not null default '[]'::jsonb
        check (jsonb_typeof(selected_ceremonies) = 'array'),
    planning_step text not null default 'requirements'
        check (length(trim(planning_step)) between 1 and 80),
    status text not null default 'planning'
        check (status in ('planning', 'completed', 'archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists wedding_projects_owner_updated_idx
    on public.wedding_projects (owner_id, updated_at desc);

alter table public.wedding_projects enable row level security;

revoke all on table public.wedding_projects from public, anon, authenticated;
grant select, insert, update, delete on table public.wedding_projects to authenticated;

drop policy if exists wedding_projects_select_own on public.wedding_projects;
create policy wedding_projects_select_own
    on public.wedding_projects for select to authenticated
    using ((select auth.uid()) = owner_id);

drop policy if exists wedding_projects_insert_own on public.wedding_projects;
create policy wedding_projects_insert_own
    on public.wedding_projects for insert to authenticated
    with check ((select auth.uid()) = owner_id);

drop policy if exists wedding_projects_update_own on public.wedding_projects;
create policy wedding_projects_update_own
    on public.wedding_projects for update to authenticated
    using ((select auth.uid()) = owner_id)
    with check ((select auth.uid()) = owner_id);

drop policy if exists wedding_projects_delete_own on public.wedding_projects;
create policy wedding_projects_delete_own
    on public.wedding_projects for delete to authenticated
    using ((select auth.uid()) = owner_id);

create or replace function public.set_wedding_projects_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = pg_catalog.now();
    return new;
end;
$$;

revoke execute on function public.set_wedding_projects_updated_at() from public, anon, authenticated;

drop trigger if exists wedding_projects_updated_at on public.wedding_projects;
create trigger wedding_projects_updated_at
    before update on public.wedding_projects
    for each row execute function public.set_wedding_projects_updated_at();