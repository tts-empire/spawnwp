#!/usr/bin/env python3
"""Preview (default) or apply the four SpawnWP site-6 goals, without duplicates."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import re
import sys
from matomo_report import load_config, call, DEFAULT_CONFIG, MatomoError
from matomo_measurement import GOALS, MARKER, records


def configure(api, apply=False):
    existing = records(api('Goals.getGoals'))
    actions = []
    since = datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
    fields = {'matchAttribute': 'match_attribute', 'pattern': 'pattern', 'patternType': 'pattern_type'}
    for key, spec in GOALS.items():
        marker = f'{MARKER}:{key};'
        matches = [g for g in existing if marker in g.get('description', '') or g.get('name') == spec['name']]
        if len(matches) > 1:
            raise ValueError(f'Multiple goals match {key}; no changes applied')
        goal = matches[0] if matches else None
        if goal and (any(goal.get(remote) != spec[local] for local, remote in fields.items())
                     or int(goal.get('case_sensitive', 0)) != 0
                     or int(goal.get('allow_multiple', 0)) != 0
                     or float(goal.get('revenue', 0)) != 0
                     or int(goal.get('event_value_as_revenue', 0)) != 0):
            raise ValueError(f'Existing goal {key} has different semantics; no changes applied')
        tracked = goal and marker in goal.get('description', '') and re.search(r'active_from=\d{4}-\d{2}-\d{2}', goal.get('description', ''))
        params = {**spec, 'caseSensitive': 0, 'revenue': 0, 'allowMultipleConversionsPerVisit': 0,
                  'useEventValueAsRevenue': 0,
                  'description': f'{marker}active_from={since}; Website intent, not a completed installation.'}
        if tracked:
            params['description'] = goal['description']
        actions.append({'key': key, 'operation': 'keep' if tracked else 'update' if goal else 'create',
                        'id': goal.get('idgoal') if goal else None, 'parameters': params})
    # Validate the whole manifest before the first mutation. A partially applied
    # run can be retried: successfully created goals are found by their marker.
    if apply:
        for item in actions:
            if item['operation'] == 'create':
                item['id'] = api('Goals.addGoal', **item['parameters'])
            elif item['operation'] == 'update':
                api('Goals.updateGoal', idGoal=item['id'], **item['parameters'])
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    config = load_config(args.config)
    if int(config['id_site']) != 6:
        parser.error('This manifest applies only to SpawnWP site 6')
    api = lambda method, **extra: call(config, method, 'day', 'today', **extra)
    print(json.dumps(configure(api, args.apply), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (MatomoError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
