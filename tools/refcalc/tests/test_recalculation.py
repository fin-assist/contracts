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

    def test_delayed_dismissal_and_final_none_then_restore_and_empty_history(self):
        b = self.backend('2026-11-01T12:00:00+03:00')
        b.upload('regular', spec.stmt('20260801', '20261031', [
            ('a', '20260815120000', -10, 'Cloud', 'Продукты'),
            ('b', '20260915120000', -10, 'Cloud', 'Продукты'),
            ('c', '20261015120000', -10, 'Cloud', 'Продукты')]))
        payment = stable_id('payment', 'Cloud')
        receipt = b.dismiss(payment, marker_ack=False, defer_calculation=True)
        self.assertEqual([], self.schema.errors('dismiss_regular_payment', receipt))
        old_false = b.analytics('month', '2026-10')
        self.assertFalse(old_false['calculation']['recalculating'])
        self.assertLess(old_false['calculation']['calc_version'], receipt['calc_version'])
        b.recalculation.ack_marker(receipt['calc_version'])
        pending = b.analytics('month', '2026-10')
        self.assertTrue(pending['state']['recalculating'])
        self.assertEqual([], self.schema.errors('/v1/analytics', pending))
        self.assertEqual(set(), b.recalculation.jobs)
        b.recalculation.start()
        b.recalculation.finish()
        ready = b.analytics('month', '2026-10')
        self.assertFalse(ready['calculation']['recalculating'])
        self.assertGreaterEqual(ready['calculation']['calc_version'], receipt['calc_version'])
        self.assertEqual('none', self.block(ready, 'regular_payments')[0]['status'])
        self.assertEqual(receipt['calc_version'], b.dismiss(payment)['calc_version'])
        restored = b.dismiss(payment, dismissed=False)
        self.assertGreater(restored['calc_version'], receipt['calc_version'])
        self.assertEqual('ready', self.block(b.analytics('month', '2026-10'), 'regular_payments')[0]['status'])
        b.delete_upload('regular')
        empty = b.analytics()
        self.assertFalse(empty['has_data'])
        self.assertEqual([], self.schema.errors('/v1/analytics', empty))
        self.assertFalse(empty['calculation']['recalculating'])
        self.assertGreater(empty['calculation']['calc_version'], restored['calc_version'])

    def test_invalid_or_foreign_payment_and_exhaustion_do_not_change_assignment(self):
        b = self.backend('2026-11-01T12:00:00+03:00')
        b.upload('one', spec.stmt('20261001', '20261031', [
            ('x', '20261015120000', -10, 'Cloud', 'Продукты')]))
        for payment, status in [('not-uuid', 400), ('00000000-0000-0000-0000-000000000001', 404)]:
            with self.assertRaises(ApiError) as caught:
                b.dismiss(payment)
            self.assertEqual(status, caught.exception.status)
            self.assertEqual(set(), b.ledger.dismissed)
        b.recalculation.current = MAX_VERSION - 2
        with self.assertRaises(ApiError) as caught:
            b.dismiss(stable_id('payment', 'Cloud'))
        self.assertEqual((500, 'INTERNAL_ERROR'), (caught.exception.status, caught.exception.code))
        self.assertEqual(set(), b.ledger.dismissed)
        self.assertEqual(set(), b.recalculation.jobs)

    def test_schema_bounds_open_status_and_required_empty_metadata(self):
        r = Recalculation()
        receipt = r.assign('p', True)
        receipt['status'] = 'future_status'
        self.assertEqual([], self.schema.errors('dismiss_regular_payment', receipt))
        for field, value in (('calc_version', -1), ('calc_version', MAX_VERSION + 1), ('text', ''), ('status', 'x' * 65)):
            self.assertTrue(self.schema.errors('dismiss_regular_payment', dict(receipt, **{field: value})))
        self.assertTrue(self.schema.errors('/v1/analytics', {'has_data': False}))
        self.assertEqual([], self.schema.errors('/v1/analytics', {'has_data': False, 'calculation': r.state()}))
