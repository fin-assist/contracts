"""FIN-418: marker ACK ends delivery; the existing background final ends calculation."""
import os
import unittest
from copy import deepcopy
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
        self.assertEqual(block, self.block(after, 'regular_payments')[0])
        unchanged = [x for x in before['blocks'] if x.get('id') != 'regular_payments']
        self.assertEqual(unchanged, [x for x in after['blocks'] if x.get('id') != 'regular_payments'])
        b.recalculation.ack_marker(update['required_calc_version'])
        self.assertEqual(set(), b.recalculation.jobs)
        b.recalculation.start(); b.recalculation.finish()
        ready = b.analytics('month', '2026-10')
        self.assertFalse(ready['calculation']['recalculating'])
        self.assertGreaterEqual(ready['calculation']['calc_version'], update['required_calc_version'])
        self.assertEqual(set(), set(b.recalculation.overlays))

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

    def test_failure_after_source_commit_retries_from_own_projection_only(self):
        b = self.regular_backend()
        old_marker = b.recalculation.marker
        with self.assertRaises(ApiError):
            self.put(b, 'E', marker_ack=False, defer_calculation=True, fail_compose=True)
        self.assertIn('E', b.ledger.dismissed)
        self.assertEqual({}, b.recalculation.overlays)
        self.assertEqual(old_marker, b.recalculation.marker)
        target = b.recalculation.assignments[stable_id('payment', 'E')][1]
        self.assertIn(target, b.recalculation.jobs)
        retry = self.put(b, 'E', marker_ack=False, defer_calculation=True)
        self.assertEqual(target, retry['required_calc_version'])
        # Destroy source operations: Apply still reads only the previously saved full metric.
        b.ledger.ops.clear()
        same = b.apply_regular_dismissal(stable_id('payment', 'E'), target, '2026-10', 'with')
        self.assertEqual(retry, same)

    def test_old_apply_after_ready_restore_new_pending_and_out_of_order_false(self):
        r = Recalculation()
        r.current = 3
        target = r.assign('p', True)['calc_version']
        r.apply_dismissal('p', target, lambda ids: self.assertIn('p', ids))
        r.apply_state(6, False)
        r.apply_state(9, False)  # Existing async restore has completed.
        r.apply_state(10, True)
        self.assertEqual(9, r.ready_version)
        r.apply_dismissal('p', target, lambda ids: self.assertNotIn('p', ids))
        r.apply_state(6, False)
        self.assertEqual(9, r.ready_version)
        r.apply_dismissal('p', 11, lambda ids: self.assertIn('p', ids))
        r.apply_state(10, True)
        self.assertIn('p', r.overlays)
        r.apply_state(15, False)
        self.assertEqual(15, r.ready_version)
        self.assertEqual({}, r.overlays)
        r.purge()
        with self.assertRaises(LookupError):
            r.apply_dismissal('p', 16, lambda _: {})

    def test_background_publishes_restored_payload_before_late_apply_without_get(self):
        b = self.regular_backend(2)
        first = self.put(b, 'A', defer_calculation=True)
        self.assertEqual(4, first['required_calc_version'])
        b.recalculation.start(); b.recalculation.finish()
        self.assertEqual(6, b.recalculation.state_version)
        b.dismiss(stable_id('payment', 'A'), dismissed=False, defer_calculation=True)
        b.recalculation.start(); b.recalculation.finish()
        self.assertEqual(9, b.recalculation.state_version)
        b.recalculation.start()
        self.assertEqual(10, b.recalculation.state_version)
        late = b.apply_regular_dismissal(stable_id('payment', 'A'), 4, '2026-10', 'with')
        self.assertEqual(['B', 'A'], [row['title'] for row in late['blocks'][0]['data']['items']])
        self.assertEqual(2100, late['blocks'][0]['data']['value'])
        self.assertIn('2', late['blocks'][0]['data']['description'])
        self.assertEqual({}, b.recalculation.overlays)

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

    def test_old_future_month_locked_and_exact_context_are_preserved(self):
        b = self.regular_backend(1)
        for date in ('2026-01', '2027-01'):
            before = self.block(b.analytics('month', date, 'without'), 'regular_payments')[0]
            update = b.dismiss(stable_id('payment', 'A'), date=date, mode='without', defer_calculation=True)
            self.assertEqual({'period': 'month', 'date': date, 'transfer_mode': 'without'}, update['params'])
            self.assertEqual('Europe/Moscow', update['time_zone'])
            if before['status'] == 'locked':
                self.assertEqual(before, update['blocks'][0])

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
