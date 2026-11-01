"""Presentation from the full saved regular metric; no operations or source-service access."""
from copy import deepcopy


def compose_regular(full_block, excluded):
    block = deepcopy(full_block)
    if block['status'] not in ('ready', 'tentative'):
        return block
    data = block['data']
    rows = [row for row in data['items'] if row['button']['payment_id'] not in excluded]
    data.pop('items_more', None)
    if not rows:
        block['status'] = 'none'
        block['empty_text'] = 'Регулярных платежей не нашли'
        block['alt_text'] = block['empty_text']
        block.pop('action', None)
        block['data'] = {k: data[k] for k in ('icon', 'title')}
        return block
    data['value'] = sum(row['value'] for row in rows)
    count = len(rows)
    noun = 'списаний' if 11 <= count % 100 <= 14 else ('списание' if count % 10 == 1 else 'списания' if count % 10 in (2, 3, 4) else 'списаний')
    data['description'] = f"в месяц · {count} {noun} каждый месяц"
    data['items'] = rows[:3]
    if len(rows) > 3:
        data['items_more'] = {'label': f'Ещё {len(rows) - 3}', 'value': sum(row['value'] for row in rows[3:]),
                              'value_kind': 'amount'}
    return block
