import unittest
from unittest.mock import patch
from matomo_measurement import collect, GOALS, MARKER, bounds
from configure_matomo_goals import configure
import matomo_report

class MeasurementTests(unittest.TestCase):
    def test_historical_and_partial_metrics_are_not_zero_conversions(self):
        api = lambda method, **kw: []
        old = collect(api, '2026-08-01', '2026-08-28', 100, [])
        self.assertIsNone(old['events']['demo_video_start']['count'])
        self.assertEqual(old['events']['visit_github']['count'], 0)
        self.assertTrue(all(v['conversions'] is None for v in old['goals'].values()))
        partial = collect(api, '2026-09-01', '2026-09-20', 100,
                          [{'label':'demo_video_50', 'nb_events':4, 'nb_visits':2}])
        self.assertEqual(partial['events']['demo_video_50']['count'], 4)
        self.assertIsNone(partial['events']['demo_video_50']['rate'])

    def test_rates_use_visits_not_repeated_events(self):
        def api(method, **kw):
            if method == 'Goals.getGoals':
                return {'5': {'idgoal':5, 'description':f'{MARKER}:demo_video;active_from=2026-09-14;'}}
            return {'nb_conversions':2,'nb_visits_converted':2}
        r = collect(api, '2026-09-15', '2026-09-20', 10,
                    [{'label':'copy_install_command','nb_events':8,'nb_visits':3}])
        self.assertEqual(r['events']['copy_install_command']['rate'], .3)
        self.assertEqual(r['goals']['demo_video']['rate'], .2)

    def test_totals_do_not_depend_on_truncated_or_missing_subtables(self):
        def api(config, method, period, date, **extra):
            if method == 'Events.getAction':
                return [{'label':'copy_install_command','nb_events':99,'nb_visits':12,'idsubdatatable':1},
                        {'label':'visit_github','nb_events':9,'nb_visits':4}]
            return [{'label':'/','nb_events':2}]
        with patch.object(matomo_report, 'call', side_effect=api):
            r = matomo_report.event_breakdown({}, 'day','2026-09-15',['copy_install_command','visit_github'])
        self.assertEqual(r['copy_install_command']['total'],99)
        self.assertEqual(r['visit_github']['total'],9)

    def test_goal_sync_is_idempotent_and_dry_run_does_not_write(self):
        goals, writes = {}, []
        def api(method, **kw):
            if method == 'Goals.getGoals':return goals
            writes.append(method)
            i = len(goals)+1
            goals[str(i)] = dict(idgoal=i,name=kw['name'],description=kw['description'],
                match_attribute=kw['matchAttribute'],pattern=kw['pattern'],pattern_type=kw['patternType'],
                allow_multiple=0,revenue=0,event_value_as_revenue=0)
            return i
        configure(api)
        self.assertEqual(writes, [])
        configure(api, True)
        self.assertEqual(len(writes),4)
        self.assertTrue(all(x['operation']=='keep' for x in configure(api,True)))
        self.assertEqual(len(writes),4)

    def test_conflicting_goal_prevents_all_writes(self):
        writes=[]
        def api(method, **kw):
            if method == 'Goals.getGoals':
                return [{'name':GOALS['demo_docs']['name'],'pattern':'wrong','idgoal':1}]
            writes.append(method)
        with self.assertRaises(ValueError):configure(api,True)
        self.assertEqual(writes,[])

    def test_goal_failures_do_not_hide_traffic_or_events(self):
        def fail(*args,**kwargs):raise RuntimeError('unavailable')
        self.assertIn('error',collect(fail,'2026-09-15','2026-09-20',0,[]))
        self.assertEqual(bounds('range','2026-08-15,2026-09-11'),('2026-08-15','2026-09-11'))

if __name__ == '__main__':unittest.main()
