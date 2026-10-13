from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from ai_event_planning_platform.config import load_supabase_settings


def create_supabase_client() -> Client:
    settings = load_supabase_settings()
    options = SyncClientOptions(auto_refresh_token=False, persist_session=False)
    return create_client(settings.url, settings.publishable_key, options=options)