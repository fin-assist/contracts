"""Bounded chart pages preserve complete navigation and isolate users/revisions."""
import datetime as dt
import os
import unittest
from zoneinfo import ZoneInfo

from refcalc.api import ApiError
from refcalc.compare import compare, redact
from refcalc.schema import SchemaCheck
from refcalc.scenario import Runner
from refcalc.util import add_months
from tests import test_spec_examples as spec


class ChartPages(unittest.TestCase):
    backend = spec.SpecExamples.backend
    block = spec.SpecExamples.block

    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaCheck(os.path.join(spec.ROOT, '../../openapi/openapi.yaml'))

    def history(self, months=121):
        b = self.backend('2026-11-01T12:00:00+03:00')
        first = add_months(dt.date(2026, 10, 1), 1 - months)
        b.upload('history', spec.stmt(first.strftime('%Y%m%d'), '20261031', [
            ('p', '20261030120000', -10, 'Person', 'Переводы')]))
        return b

    def chart(self, b, period='month', date=None, mode='with'):
        return self.block(b.analytics(period, date, mode), 'monthly_chart')[0]

    def test_pages_cover_every_month_both_directions_without_duplicates(self):
        for count in (60, 61, 120, 121):
            with self.subTest(count=count):
                b = self.history(count)
                chart = self.chart(b)
                revision = chart['data']['page']['revision']
                pages = [chart['data']['points']]
                page = chart['data']['page']
                self.assertNotIn('next', page)
                self.assertEqual(60, len(pages[0]))
                while 'previous' in page:
                    response = b.chart_page(page['previous'])
                    self.assertEqual([], self.schema.errors('/v1/analytics/charts/monthly_chart', response))
                    data = response['block']['data']
                    self.assertNotIn('notes', data)
                    self.assertNotIn('highlight_index', data)
                    self.assertEqual(revision, data['page']['revision'])
                    self.assertLessEqual(len(data['points']), 60)
                    self.assertEqual(pages[-1], b.chart_page(data['page']['next'])['block']['data']['points'])
                    pages.append(data['points'])
                    page = data['page']
                points = [p for page_points in reversed(pages) for p in page_points]
                self.assertEqual(count, len(points))
                self.assertEqual(count, len({p['action']['date'] for p in points}))
                for left, right in zip(points, points[1:]):
                    self.assertEqual(left['range']['to'], right['range']['from'])
                oldest = self.chart(b, date=points[0]['action']['date'])['data']
                self.assertEqual(pages[-1], oldest['points'])
                self.assertEqual(revision, oldest['page']['revision'])
                self.assertEqual(0, oldest['highlight_index'])
                for period in ('quarter', 'year', 'all_time', 'rolling_year', 'rolling_quarter'):
                    other = self.chart(b, period)['data']
                    self.assertEqual(pages[0], other['points'])
                    self.assertEqual(revision, other['page']['revision'])

    def test_historical_history_is_bounded_and_gaps_are_distinct_from_zero(self):
        b = self.backend('2026-11-01T12:00:00+03:00')
        b.upload('old', spec.stmt('19000102', '19000131', [('x', '19000103120000', -1, 'Shop', 'Продукты')]))
        b.upload('zero', spec.stmt('20261001', '20261031', []))
        chart = self.chart(b)
        points = chart['data']['points']
        self.assertEqual(60, len(points))
        self.assertTrue(all(p['coverage'] == 'no_data' and p['value'] is None and 'action' not in p for p in points[:-1]))
        self.assertEqual((0, '2026-10'), (points[-1]['value'], points[-1]['action']['date']))
        early = self.chart(b, date='1900-01')
        self.assertEqual([], self.schema.errors('/v1/analytics', b.analytics('month', '1900-01')))
        self.assertIn('next', early['data']['page'])
        self.assertNotIn('previous', early['data']['page'])
        self.assertEqual('1899-12-31T21:29:43+00:00', early['data']['points'][0]['range']['from'])

    def test_cursor_is_owner_bound_and_cannot_be_reconstructed_or_extended(self):
        b, other = self.history(), self.history()
        cursor = self.chart(b)['data']['page']['previous']
        for owner, token, code in ((b, None, 'REQUIRED'), (b, '', 'REQUIRED'),
                                   (b, 'x' * 2049, 'INVALID_FORMAT'), (b, cursor + 'x', 'INVALID_FORMAT'),
                                   (other, cursor, 'INVALID_FORMAT')):
            with self.subTest(code=code, owner=owner is b), self.assertRaises(ApiError) as caught:
                owner.chart_page(token)
            self.assertEqual([{'field': 'cursor', 'code': code}], caught.exception.details)
        runner = Runner.__new__(Runner)
        runner.backend = b
        for query, field in (('cursor=x&cursor=y', 'cursor'), ('cursor=' + cursor + '&transfer_mode=without', 'transfer_mode')):
            with self.assertRaises(ApiError) as caught:
                runner.get('/v1/analytics/charts/monthly_chart?' + query)
            self.assertEqual([{'field': field, 'code': 'INVALID_FORMAT'}], caught.exception.details)

    def test_revision_changes_for_mode_day_category_timezone_and_deletion(self):
        b = self.history()
        with_page = self.chart(b)['data']['page']
        without_page = self.chart(b, mode='without')['data']['page']
        self.assertNotEqual(with_page['revision'], without_page['revision'])
        # A valid cursor preserves its mode; a late response is distinguishable from the active screen revision.
        self.assertEqual(with_page['revision'], b.chart_page(with_page['previous'])['block']['data']['page']['revision'])
        for change in ('category', 'day', 'timezone', 'delete'):
            page = self.chart(b)['data']['page']
            if change == 'category':
                b.change_category(spec.CUR, 'p', 'cat_supermarkets')
            elif change == 'day':
                b.now += dt.timedelta(days=1)
            elif change == 'timezone':
                b.zone = b.ledger.zone = ZoneInfo('Asia/Tokyo')
            else:
                b.delete_upload('history')
            with self.subTest(change=change), self.assertRaises(ApiError) as caught:
                b.chart_page(page['previous'])
            self.assertEqual([{'field': 'cursor', 'code': 'OUT_OF_RANGE'}], caught.exception.details)
        self.assertEqual({'has_data': False, 'calculation': b.recalculation.state()}, b.analytics())

    def test_page_schema_and_comparison_keep_bound_and_required_opaque_metadata(self):
        b = self.history()
        response = b.chart_page(self.chart(b)['data']['page']['previous'])
        expected = redact(response)
        response['block']['data']['page'].pop('revision')
        self.assertTrue(compare(expected, response))
        self.assertTrue(self.schema.errors('/v1/analytics/charts/monthly_chart', response))
        response = b.chart_page(self.chart(b)['data']['page']['previous'])
        response['block']['data']['points'] *= 2
        self.assertTrue(self.schema.errors('/v1/analytics/charts/monthly_chart', response))
        # The 60-point limit belongs to monthly_chart, not every chart primitive.
        screen = b.analytics()
        balance = self.block(screen, 'balance')[0]
        balance['data']['points'] *= 11
        self.assertEqual(66, len(balance['data']['points']))
        self.assertEqual([], self.schema.errors('/v1/analytics', screen))
        response['block']['data']['page']['revision'] = None
        self.assertTrue(self.schema.errors('/v1/analytics/charts/monthly_chart', response))

    def test_historical_download_offsets_are_valid_rfc3339_instants(self):
        b = self.history()
        result = b.import_config('1900-01-01', '1900-01-01')
        self.assertEqual({'from': '1899-12-31T21:29:43+00:00', 'to': '1900-01-01T21:29:43+00:00'}, result['suggested_period'])
        self.assertEqual([], self.schema.errors('/v1/statements/config', result))

    def test_historical_calendar_labels_decode_in_profile_iana_zone(self):
        b = self.backend('2026-11-01T12:00:00+03:00')
        b.upload('january', spec.stmt('19000101', '19000131', [
            ('x', '19000102120000', -1, 'Shop', 'Продукты')]))
        screen = b.analytics('month', '1900-01')
        chart = self.block(screen, 'monthly_chart')[0]['data']
        point = chart['points'][chart['highlight_index']]
        # Independent consumer decoding: don't use the producer's iso/day_of helpers.
        zone = ZoneInfo('Europe/Moscow')
        for bounds in (screen['period']['range'], point['range']):
            start = dt.datetime.fromisoformat(bounds['from'])
            end = dt.datetime.fromisoformat(bounds['to'])
            self.assertEqual(dt.date(1899, 12, 31), start.date())
            first = start.astimezone(zone).date()
            last = end.astimezone(zone).date() - dt.timedelta(days=1)
            self.assertEqual('01.01.1900–31.01.1900',
                             first.strftime('%d.%m.%Y') + '–' + last.strftime('%d.%m.%Y'))
            self.assertEqual('1900-01', first.strftime('%Y-%m'))
            self.assertEqual(point['action']['date'], first.strftime('%Y-%m'))
        for field, expected in [('data_from', dt.date(1900, 1, 1)), ('data_to', dt.date(1900, 1, 31))]:
            instant = dt.datetime.fromisoformat(screen['period'][field])
            self.assertEqual(expected, instant.astimezone(zone).date())
        download = b.import_config('1900-01-01', '1900-01-01')['suggested_period']
        self.assertEqual(dt.date(1900, 1, 1), dt.datetime.fromisoformat(download['from']).astimezone(zone).date())
        self.assertEqual(dt.date(1900, 1, 1), dt.datetime.fromisoformat(download['to']).astimezone(zone).date() - dt.timedelta(days=1))

    def test_manual_own_transfer_and_refund_changes_invalidate_pages(self):
        b = self.history()
        for category, amount in (('cat_own_transfer', 0), ('cat_supermarkets', 1000)):
            old = self.chart(b)['data']['page']
            b.change_category(spec.CUR, 'p', category)
            current = self.chart(b)['data']
            self.assertNotEqual(old['revision'], current['page']['revision'])
            self.assertEqual(amount, current['points'][-1]['value'])
            with self.assertRaises(ApiError) as caught:
                b.chart_page(old['previous'])
            self.assertEqual('OUT_OF_RANGE', caught.exception.details[0]['code'])
        b.upload('refund', spec.stmt('20261001', '20261031', [('r', '20261031120000', 5, 'Person', 'Возврат')]))
        old = self.chart(b)['data']
        self.assertEqual(500, old['points'][-1]['value'])
        b.change_category(spec.CUR, 'r', 'cat_transfers')
        current = self.chart(b)['data']
        self.assertEqual(1000, current['points'][-1]['value'])
        self.assertNotEqual(old['page']['revision'], current['page']['revision'])
        with self.assertRaises(ApiError):
            b.chart_page(old['page']['previous'])
        # Container insertion order is not part of the observed revision.
        b.ledger.ops = dict(reversed(list(b.ledger.ops.items())))
        b.ledger.manual = dict(reversed(list(b.ledger.manual.items())))
        self.assertEqual(current['page']['revision'], self.chart(b)['data']['page']['revision'])
