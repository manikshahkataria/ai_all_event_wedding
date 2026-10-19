"""Application entry point: native page routing and the existing Supabase guard."""

from ai_event_planning_platform.home import render_home


if __name__ == "__main__":
    render_home()
