"""FIN-420 source calculation + pure composition; FIN-418 marker/background stays independent."""
import os
import unittest
from copy import deepcopy
from zoneinfo import ZoneInfo
from refcalc.api import ApiError
from refcalc.ledger import stable_id
from refcalc.recalculation import MAX_VERSION, Recalculation
from refcalc.schema import SchemaCheck
from tests import test_spec_examples as spec


class RecalculationContract(unittest.TestCase):
    backend = spec.SpecExamples.backend
    block = spec.SpecExamples.block
    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaCheck(os.path.join(spec.ROOT, '../../openapi/openapi.yaml'))

    def test_ack_ends_only_marker_job_and_retry_preserves_original_target(self):
        r = Recalculation()
        r.calculate_now()
        current = r.current
        receipt = r.assign('payment', True, False)
        self.assertEqual('pending', receipt['status'])
        self.assertEqual(current + 1, receipt['calc_version'])
        self.assertEqual(current, r.current)
        self.assertFalse(r.state()['recalculating'])
        self.assertLess(r.state()['calc_version'], receipt['calc_version'])
        self.assertEqual({receipt['calc_version']}, r.jobs)
        r.ack_marker(receipt['calc_version'])
        self.assertEqual(set(), r.jobs)
        self.assertTrue(r.state()['recalculating'])
        self.assertEqual(current, r.state()['calc_version'])
        r.start()
        r.finish()
        self.assertEqual(receipt['calc_version'], r.assign('payment', True)['calc_version'])
        self.assertFalse(r.state()['recalculating'])
        self.assertEqual(set(), r.jobs)

    def test_mutation_before_pending_between_pending_and_build_after_complete(self):
        for stage in ('before', 'between', 'after'):
            with self.subTest(stage=stage):
                r = Recalculation()
                r.calculate_now()
                if stage in ('between', 'after'):
                    r.start()
                if stage == 'after':
                    r.finish()
                current = r.current
                target = r.assign('p', True)['calc_version']
                self.assertEqual(current + 1, target)
                self.assertEqual(current, r.current)  # No counter gap, original barriers remain valid.
                if stage != 'between':
                    r.start()
                r.finish()
                self.assertGreater(r.state()['calc_version'], target)
                self.assertFalse(r.state()['recalculating'])

    def test_coalescing_old_false_late_marker_and_opposite_assignment(self):
        r = Recalculation()
        r.calculate_now()
        old_final = r.current
        first = r.assign('p', True)['calc_version']
        self.assertEqual(first, r.assign('q', True)['calc_version'])
        r.apply_state(old_final, False)
        self.assertTrue(r.state()['recalculating'])
        r.start()
        restored = r.assign('p', False)['calc_version']
        self.assertEqual(r.current + 1, restored)
        r.finish()
        r.mark(first)
        r.mark(restored)
        r.apply_state(old_final, False)
        self.assertFalse(r.state()['recalculating'])
        self.assertGreaterEqual(r.state()['calc_version'], restored)
        self.assertEqual(restored, r.assign('p', False)['calc_version'])

    def test_noop_overflow_validation_and_tombstone(self):
        r = Recalculation()
        self.assertEqual(0, r.assign('p', False)['calc_version'])
        self.assertEqual({}, r.assignments)
        self.assertEqual(set(), r.jobs)
        for version in (-1, 0, MAX_VERSION + 1, True):
            with self.assertRaises(ValueError):
                r.mark(version)
        r.current = MAX_VERSION - 2
        before = deepcopy(r.__dict__)
        with self.assertRaises(OverflowError):
            r.assign('p', True)
        self.assertEqual(before, r.__dict__)
        r = Recalculation()
        target = r.assign('p', True, False)['calc_version']
        r.purge()
        r.ack_marker(target)
        r.apply_state(7, False)
        self.assertEqual({'calc_version': 0, 'recalculating': False}, r.state())
        self.assertEqual(set(), r.jobs)
        with self.assertRaises(LookupError):
            r.assign('p', False)

    def regular_backend(self, count=5):
        b = self.backend('2026-11-01T12:00:00+03:00')
        rows = [(f'{name}-{month}', f'2026{month}15120000', -(10 + index), name, 'Продукты')
                for index, name in enumerate('ABCDE'[:count]) for month in ('08', '09', '10')]
        b.upload('regular', spec.stmt('20260801', '20261031', rows))
        return b

    def put(self, b, name, **kw):
        return b.dismiss(stable_id('payment', name), date='2026-10', mode='with', **kw)

    def test_updated_full_block_precedes_background_and_immediate_get_does_not_restore(self):
        b = self.regular_backend()
        before = b.analytics('month', '2026-10')
        update = self.put(b, 'E', marker_ack=False, defer_calculation=True)
        self.assertEqual([], self.schema.errors('dismiss_regular_payment', update))
        block = update['blocks'][0]
        self.assertEqual(['D', 'C', 'B'], [x['title'] for x in block['data']['items']])
        self.assertEqual(4600, block['data']['value'])
        self.assertEqual({'label': 'Ещё 1', 'value': 1000, 'value_kind': 'amount'}, block['data']['items_more'])
        self.assertIn('4', block['data']['description'])
        self.assertGreater(update['required_calc_version'], before['calculation']['calc_version'])
        self.assertEqual(before['calculation']['calc_version'], b.recalculation.state_version)
        self.assertEqual({update['required_calc_version']}, b.recalculation.jobs)  # Apply is not BSD job ACK.
        after = b.analytics('month', '2026-10')
        self.assertTrue(after['calculation']['recalculating'])
        self.assertEqual(self.block(before, 'regular_payments')[0], self.block(after, 'regular_payments')[0])  # GET is still the async baseline while true.
        unchanged = [x for x in before['blocks'] if x.get('id') != 'regular_payments']
        self.assertEqual(unchanged, [x for x in after['blocks'] if x.get('id') != 'regular_payments'])
        b.recalculation.ack_marker(update['required_calc_version'])
        self.assertEqual(set(), b.recalculation.jobs)
        b.recalculation.start(); b.recalculation.finish()
        ready = b.analytics('month', '2026-10')
        self.assertFalse(ready['calculation']['recalculating'])
        self.assertGreaterEqual(ready['calculation']['calc_version'], update['required_calc_version'])

    def test_changed_false_retry_multiple_ids_same_target_and_last_none(self):
        b = self.regular_backend(2)
        first = self.put(b, 'A', defer_calculation=True)
        retry = self.put(b, 'A', defer_calculation=True)
        self.assertEqual(first, retry)
        last = self.put(b, 'B', defer_calculation=True)
        self.assertEqual(first['required_calc_version'], last['required_calc_version'])
        block = last['blocks'][0]
        self.assertEqual('none', block['status'])
        self.assertNotIn('value', block['data'])
        self.assertNotIn('items_more', block['data'])
        self.assertNotIn('action', block)
        self.assertEqual([], self.schema.errors('dismiss_regular_payment', last))
        b.recalculation.start(); b.recalculation.finish()
        self.assertEqual('none', self.put(b, 'A')['blocks'][0]['status'])
        self.assertFalse(b.recalculation.state()['recalculating'])
        self.assertIsNone(b.dismiss(stable_id('payment', 'A'), dismissed=False))  # Existing bodyless DELETE.
        self.assertEqual('ready', self.block(b.analytics('month', '2026-10'), 'regular_payments')[0]['status'])

    def test_failure_after_source_commit_retries_fresh_source_then_composes_without_database(self):
        b = self.regular_backend()
        old_marker = b.recalculation.marker
        with self.assertRaises(ApiError):
            self.put(b, 'E', marker_ack=False, defer_calculation=True, fail_compose=True)
        self.assertIn('E', b.ledger.dismissed)
        self.assertEqual(old_marker, b.recalculation.marker)
        target = b.recalculation.assignments[stable_id('payment', 'E')][1]
        self.assertIn(target, b.recalculation.jobs)
        b.ledger.dismissed.add('D')  # A newer source change between attempts; own analytics is stale.
        retry = self.put(b, 'E', marker_ack=False, defer_calculation=True)
        self.assertEqual(target, retry['required_calc_version'])
        self.assertEqual(['C', 'B', 'A'], [r['title'] for r in retry['blocks'][0]['data']['items']])
        projection = b._fresh_regular_projection('2026-10', 'with')
        b.ledger.ops.clear()
        b._regular_snapshots.clear()
        b._source_regular_block = lambda *_: self.fail('composition read source')
        same = b.apply_regular_dismissal(target, projection, stable_id('payment', 'E'))
        self.assertEqual(retry, same)

    def test_calculation_or_payload_failure_rolls_back_new_assignment_and_jobs(self):
        for injected in ('fail_calculate', 'fail_payload'):
            b = self.regular_backend()
            state = deepcopy(b.recalculation.__dict__)
            with self.assertRaises(ApiError) as caught:
                self.put(b, 'A', **{injected: True})
            self.assertEqual((500, injected == 'fail_calculate'), (caught.exception.status, caught.exception.retryable))
            self.assertNotIn('A', b.ledger.dismissed)
            for key in ('assignments', 'jobs', 'marker', 'current', 'calculation_pending'):
                self.assertEqual(state[key], b.recalculation.__dict__[key])
            self.put(b, 'A', defer_calculation=True)
            with self.assertRaises(ApiError):
                self.put(b, 'A', **{injected: True})
            self.assertIn('A', b.ledger.dismissed)  # An earlier committed effect is never undone.

    def test_failed_apply_keeps_durable_calculation_for_retry_or_independent_worker(self):
        for retry_first in (True, False):
            with self.subTest(retry_first=retry_first):
                b = self.regular_backend(2)
                with self.assertRaises(ApiError):
                    self.put(b, 'A', fail_compose=True)  # No defer flag.
                target = b.recalculation.assignments[stable_id('payment', 'A')][1]
                self.assertTrue(b.recalculation.calculation_pending)
                if not retry_first:
                    b.recalculation.run_pending()  # Worker does not require a successful Apply.
                retry = self.put(b, 'A')
                self.assertEqual(target, retry['required_calc_version'])
                ready = b.analytics('month', '2026-10')
                self.assertFalse(ready['calculation']['recalculating'])
                self.assertGreaterEqual(ready['calculation']['calc_version'], target)
                self.assertFalse(b.recalculation.calculation_pending)
                self.assertEqual(['B'], [row['title'] for row in self.block(ready, 'regular_payments')[0]['data']['items']])

    def test_updated_actions_search_live_assignments_without_get_or_background(self):
        b = self.regular_backend(2)
        update = self.put(b, 'A', defer_calculation=True)
        block = update['blocks'][0]
        found = b.search(block['action']['filter'])
        self.assertEqual(['B'], [row['title'] for row in found['items']])
        self.assertEqual(block['data']['value'], sum(row['amount'] for row in found['items']))
        for row in block['data']['items']:
            own = b.search(row['action']['filter'])
            self.assertEqual([row['title']], [op['title'] for op in own['items']])
            self.assertEqual(row['value'], sum(op['amount'] for op in own['items']))
        self.assertTrue(b.recalculation.calculation_pending)
        self.assertLess(b.recalculation.state_version, update['required_calc_version'])

    def test_late_supplied_projection_never_replaces_persisted_financial_state(self):
        b = self.regular_backend(2)
        self.put(b, 'A', defer_calculation=True)
        projection = b._fresh_regular_projection('2026-10', 'with')
        b.recalculation.start(); b.recalculation.finish()
        b.dismiss(stable_id('payment', 'A'), dismissed=False, defer_calculation=True)
        b.recalculation.start(); b.recalculation.finish()
        restored = b.analytics('month', '2026-10')
        b.recalculation.start()
        state = b.recalculation.state()
        late = b.apply_regular_dismissal(4, projection, stable_id('payment', 'A'))
        self.assertEqual(['B'], [row['title'] for row in late['blocks'][0]['data']['items']])
        self.assertEqual(state, b.recalculation.state())
        self.assertEqual(['B', 'A'], [row['title'] for row in self.block(restored, 'regular_payments')[0]['data']['items']])
        b.recalculation.apply_state(6, False)
        self.assertEqual(state, b.recalculation.state())
        b.recalculation.purge()
        with self.assertRaises(LookupError):
            b.apply_regular_dismissal(4, projection, stable_id('payment', 'A'))

    def test_supplied_payload_is_bound_to_user_and_displayed_params(self):
        b = self.regular_backend(2)
        projection = b._fresh_regular_projection('2026-10', 'with')
        for field, value in [('user_id', 'other-user'), ('currency', 'USD'), ('time_zone', 'Local'), ('time_zone', 'Not/A_Zone'),
                             ('metrics', []), ('metrics', [{'id': 'insights.regular_payments', 'full_block': {}}]), ('params', {'period': 'year'})]:
            bad = deepcopy(projection); bad[field] = value
            with self.assertRaises(ApiError) as caught:
                b.apply_regular_dismissal(4, bad, stable_id('payment', 'A'))
            self.assertEqual((500, False), (caught.exception.status, caught.exception.retryable))
            self.assertEqual(0, b.recalculation.marker)
        for bad in (None, {}, projection):
            with self.assertRaises(ApiError):
                b.apply_regular_dismissal(4, bad, stable_id('payment', 'A'), expected_params={'period': 'month', 'date': '2026-09', 'transfer_mode': 'without'})
        self.assertEqual(0, b.recalculation.marker)

    def test_metric_collection_and_dismissed_id_binding_reject_invalid_without_effect(self):
        b = self.regular_backend(2)
        payment = stable_id('payment', 'A')
        stale = b._fresh_regular_projection('2026-10', 'with')
        for status in ('ready', 'tentative'):
            payload = deepcopy(stale)
            payload['metrics'][0]['full_block']['status'] = status
            with self.assertRaises(ApiError) as caught:
                b.apply_regular_dismissal(4, payload, payment)
            self.assertEqual((500, False), (caught.exception.status, caught.exception.retryable))
            self.assertEqual(0, b.recalculation.marker)
        self.put(b, 'A', defer_calculation=True)
        projection = b._fresh_regular_projection('2026-10', 'with')
        for metrics in (None, [], projection['metrics'] * 2,
                        [dict(projection['metrics'][0], id='future.metric')]):
            invalid = deepcopy(projection)
            invalid['metrics'] = metrics
            before = b.recalculation.state()
            with self.assertRaises(ApiError) as caught:
                b.apply_regular_dismissal(4, invalid, payment)
            self.assertEqual((500, False), (caught.exception.status, caught.exception.retryable))
            self.assertEqual(before, b.recalculation.state())
        valid = b.apply_regular_dismissal(4, projection, payment)
        self.assertEqual(['B'], [row['title'] for row in valid['blocks'][0]['data']['items']])

    def test_apply_none_and_locked_remain_valid_when_source_context_disappears(self):
        for month, status in [('2026-08', 'locked'), ('2026-10', 'none')]:
            b = self.regular_backend(1)
            payment = stable_id('payment', 'A')
            update = b.dismiss(payment, date=month, mode='with', defer_calculation=True)
            projection = b._fresh_regular_projection(month, 'with')
            b.delete_upload('regular')
            b._regular_snapshots.clear()
            result = b.apply_regular_dismissal(update['required_calc_version'], projection, payment)
            self.assertEqual(status, result['blocks'][0]['status'])
            self.assertEqual(update, result)
            self.assertEqual([], self.schema.errors('dismiss_regular_payment', result))

    def test_invalid_context_before_assignment_foreign_id_and_exhaustion(self):
        b = self.regular_backend(1)
        payment = stable_id('payment', 'A')
        for date, mode in [(None, 'with'), ('2026', 'with'), ('0000-01', 'with'), ('2026-13', 'with'), ('2026-10', None), ('2026-10', 'other')]:
            with self.assertRaises(ApiError) as caught:
                b.dismiss(payment, date=date, mode=mode)
            self.assertEqual(400, caught.exception.status)
            self.assertEqual(set(), b.ledger.dismissed)
        for payment, status in [('not-uuid', 400), ('00000000-0000-0000-0000-000000000001', 404)]:
            with self.assertRaises(ApiError) as caught:
                b.dismiss(payment, date='2026-10', mode='with')
            self.assertEqual(status, caught.exception.status)
        b.recalculation.current = MAX_VERSION - 2
        with self.assertRaises(ApiError) as caught:
            self.put(b, 'A')
        self.assertEqual((500, 'INTERNAL_ERROR'), (caught.exception.status, caught.exception.code))
        self.assertEqual(set(), b.ledger.dismissed)

    def test_nonexistent_month_before_new_assignment_is_not_a_saved_change_error(self):
        b = self.regular_backend(1)
        for date in ('2026-01', '2027-01', '9999-12'):
            with self.assertRaises(ApiError) as caught:
                b.dismiss(stable_id('payment', 'A'), date=date, mode='without', defer_calculation=True)
            self.assertEqual((400, 'VALIDATION_ERROR', False),
                             (caught.exception.status, caught.exception.code, caught.exception.retryable))
            self.assertEqual([{'field': 'date', 'code': 'OUT_OF_RANGE'}], caught.exception.details)
            self.assertNotIn('A', b.ledger.dismissed)
            self.assertEqual({}, b.recalculation.assignments)

    def test_context_removed_after_saved_assignment_reports_truthful_422(self):
        b = self.regular_backend(1)
        self.put(b, 'A', defer_calculation=True)
        b.delete_upload('regular')
        with self.assertRaises(ApiError) as caught:
            self.put(b, 'A')
        self.assertEqual((422, 'ANALYTICS_CONTEXT_UNAVAILABLE', False),
                         (caught.exception.status, caught.exception.code, caught.exception.retryable))
        self.assertIsNone(caught.exception.details)
        self.assertIn('A', b.ledger.dismissed)
        b.recalculation.run_pending()
        self.assertFalse(b.analytics()['has_data'])

    def test_missing_analytics_snapshot_does_not_block_supplied_fresh_metric(self):
        b = self.regular_backend(2)
        self.assertEqual({}, b._regular_snapshots)
        update = self.put(b, 'A', defer_calculation=True)
        self.assertEqual(['B'], [r['title'] for r in update['blocks'][0]['data']['items']])
        self.assertEqual({}, b._regular_snapshots)

    def test_existing_covered_month_preserves_lock_and_response_context(self):
        b = self.regular_backend(1)
        before = self.block(b.analytics('month', '2026-08', 'without'), 'regular_payments')[0]
        update = b.dismiss(stable_id('payment', 'A'), date='2026-08', mode='without', defer_calculation=True)
        self.assertEqual('locked', before['status'])
        self.assertEqual(before, update['blocks'][0])
        self.assertEqual({'period': 'month', 'date': '2026-08', 'transfer_mode': 'without'}, update['params'])
        self.assertEqual('Europe/Moscow', update['time_zone'])

    def test_source_window_uses_disjoint_full_months_in_both_modes_and_own_zone(self):
        for mode in ('with', 'without'):
            for zone, offset in [('Europe/Moscow', '[+3:MSK]'), ('Asia/Vladivostok', '[+10:VLAT]')]:
                with self.subTest(mode=mode, zone=zone):
                    b = self.backend('2027-01-01T12:00:00+03:00')
                    b.zone = b.ledger.zone = ZoneInfo(zone)
                    for month in ('08', '10', '12'):
                        rows = [(name + month, f'2026{month}15120000', -amount, name, 'Продукты')
                                for name, amount in [('A', 10), ('B', 20)]]
                        raw = spec.stmt('2026' + month + '01', '2026' + month + '31', rows).replace('[+3:MSK]', offset)
                        b.upload(month, raw)
                    update = b.dismiss(stable_id('payment', 'A'), date='2026-12', mode=mode, defer_calculation=True)
                    self.assertEqual(zone, update['time_zone'])
                    self.assertEqual(mode, update['params']['transfer_mode'])
                    self.assertEqual('ready', update['blocks'][0]['status'])
                    self.assertEqual(2000, update['blocks'][0]['data']['value'])
                    self.assertEqual(['B'], [r['title'] for r in update['blocks'][0]['data']['items']])
                    self.assertEqual([], self.schema.errors('dismiss_regular_payment', update))

    def test_update_schema_bounds_duplicates_and_required_empty_metadata(self):
        b = self.regular_backend(1)
        update = self.put(b, 'A')
        for value in (0, -1, MAX_VERSION + 1):
            self.assertTrue(self.schema.errors('dismiss_regular_payment', dict(update, required_calc_version=value)))
        for blocks in ([], update['blocks'] * 2, update['blocks'] * 17):
            self.assertTrue(self.schema.errors('dismiss_regular_payment', dict(update, blocks=blocks)))
        for field in ('params', 'currency', 'time_zone', 'required_calc_version'):
            bad = dict(update); bad.pop(field)
            self.assertTrue(self.schema.errors('dismiss_regular_payment', bad))
        self.assertTrue(self.schema.errors('/v1/analytics', {'has_data': False}))
        self.assertEqual([], self.schema.errors('/v1/analytics', {'has_data': False, 'calculation': b.recalculation.state()}))
