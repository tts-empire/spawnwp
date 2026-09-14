"""Website intent metrics; never interpret these as completed installations.

Also shipped with the private outreach reporter; keep that copy in sync.
"""
from datetime import date, timedelta
import calendar
import re

TRACKING_SINCE = '2026-09-14'
EVENTS = {
    'open_install_section': 'Apertura sezione installazione',
    'visit_requirements': 'Clic verso requisiti',
    'visit_installation': 'Clic verso guida installazione',
    'copy_install_command': 'Comando installazione copiato',
    'visit_github': 'Clic verso GitHub',
    'visit_product_demos': 'Clic verso pagina demo',
    'visit_demo_module_docs': 'Clic verso documentazione demo',
    'demo_video_start': 'Video demo avviato',
    'demo_video_50': 'Video demo visto almeno al 50%',
    'demo_video_complete': 'Video demo arrivato alla fine',
}
NEW_EVENTS = {'open_install_section', 'demo_video_start', 'demo_video_50', 'demo_video_complete'}
GOALS = {
    'installation_guide': dict(name='Guida installazione consultata', matchAttribute='url',
        pattern=r'^https://spawnwp\.com/docs/installation/(?:[?#].*)?$', patternType='regex'),
    'installation_command': dict(name='Comando installazione copiato', matchAttribute='event_action',
        pattern='copy_install_command', patternType='exact'),
    'demo_video': dict(name='Video demo visto almeno al 50%', matchAttribute='event_action',
        pattern='demo_video_50', patternType='exact'),
    'demo_docs': dict(name='Documentazione demo consultata', matchAttribute='url',
        pattern=r'^https://spawnwp\.com/docs/(?:modules|configuring-turnstile)/(?:[?#].*)?$', patternType='regex'),
}
MARKER = 'spawnwp-measurement-v1'


def records(payload):
    if isinstance(payload, dict):
        payload = list(payload.values())
    return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []


def bounds(period, value):
    """Resolve report intervals without treating historical data as fresh zeros."""
    today = date.today()
    if period == 'range':
        start, end = value.split(',')
        return date.fromisoformat(start).isoformat(), date.fromisoformat(end).isoformat()
    last = re.fullmatch(r'last(\d+)', value)
    if last:
        return (today - timedelta(days=int(last[1]) - 1)).isoformat(), today.isoformat()
    day = today if value == 'today' else today - timedelta(days=1) if value == 'yesterday' else date.fromisoformat(value)
    if period == 'week':
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
    elif period == 'month':
        start, end = day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    elif period == 'year':
        start, end = day.replace(month=1, day=1), day.replace(month=12, day=31)
    else:
        start = end = day
    return start.isoformat(), min(end, today).isoformat()


def availability(start, end, since):
    if not since or end < since:
        return 'unavailable'
    # Activation day itself is partial: activation can happen during the day.
    return 'partial' if start <= since else 'available'


def collect(api, start, end, total_visits, event_rows):
    result = {'tracking_since': TRACKING_SINCE, 'events': {}, 'goals': {}}
    by_action = {r.get('label'): r for r in records(event_rows)}
    for action, label in EVENTS.items():
        status = availability(start, end, TRACKING_SINCE) if action in NEW_EVENTS else 'available'
        row = by_action.get(action, {})
        count, visits = int(row.get('nb_events', 0)), int(row.get('nb_visits', 0))
        result['events'][action] = dict(name=label, status=status,
            count=count if status != 'unavailable' else None,
            visits=visits if status != 'unavailable' else None,
            rate=visits / total_visits if total_visits and status == 'available' else None)
    try:
        goals = records(api('Goals.getGoals'))
    except Exception:
        result['error'] = 'Obiettivi Matomo non disponibili'
        goals = []
    for key, spec in GOALS.items():
        matches = [g for g in goals if f'{MARKER}:{key};' in g.get('description', '')]
        goal = matches[0] if len(matches) == 1 else {}
        match = re.search(r'active_from=(\d{4}-\d{2}-\d{2})', goal.get('description', ''))
        since = match[1] if match else None
        status = availability(start, end, since)
        metrics = {}
        if status != 'unavailable':
            try:
                metrics = api('Goals.get', idGoal=goal['idgoal'])
                if not isinstance(metrics, dict):
                    metrics = {}
            except Exception:
                status = 'unavailable'
        visits = int(metrics.get('nb_visits_converted', 0))
        result['goals'][key] = dict(name=spec['name'], status=status, active_from=since,
            id=goal.get('idgoal'), conversions=int(metrics.get('nb_conversions', 0)) if status != 'unavailable' else None,
            visits=visits if status != 'unavailable' else None,
            rate=visits / total_visits if total_visits and status == 'available' else None)
    return result
