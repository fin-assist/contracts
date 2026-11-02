"""Presentation-only adapter of the fresh source result; financial totals/counts stay source-owned.

The executable model carries source output in its full pre-top3 block form. Production uses
MetricProjection/MetricSnapshot; protobuf size and transport bindings need runtime tests.
"""
from copy import deepcopy


def compose_regular(full_block):
    block = deepcopy(full_block)
    if block['status'] not in ('ready', 'tentative'):
        return block
    data = block['data']
    rows = data['items']
    data.pop('items_more', None)
    data['items'] = rows[:3]
    if len(rows) > 3:
        data['items_more'] = {'label': f'Ещё {len(rows) - 3}', 'value': sum(row['value'] for row in rows[3:]),
                              'value_kind': 'amount'}
    return block
