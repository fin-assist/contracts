"""Observable FIN-418 marker/assignment model; no database or network simulation."""

MAX_VERSION = 2_147_483_647


class Recalculation:
    def __init__(self, on_publish=None):
        self.on_publish = on_publish
        self.current = 0
        self.state_version = 0
        self.stored_flag = False
        self.marker = 0
        self.deleted = False
        self.assignments = {}
        self.ready_version = 0
        self.jobs = set()
        self.calculation_pending = False

    def assign(self, payment, dismissed, acknowledged=True):
        if self.deleted:
            raise LookupError('deleted user')
        previous, target = self.assignments.get(payment, (False, 0))
        if previous != dismissed:
            if self.current > MAX_VERSION - 3:
                raise OverflowError('calculation version exhausted')
            target = self.current + 1  # Prediction only: the producer counter is unchanged.
            self.assignments[payment] = (dismissed, target)
            self.jobs.add(target)
            self.calculation_pending = True
        if acknowledged and target:
            self.ack_marker(target)
        return {'status': 'accepted' if acknowledged or target == 0 else 'pending',
                'calc_version': target,
                'text': ('Изменение сохранено. Аналитика обновляется.' if acknowledged or target == 0 else
                         'Изменение сохранено. Обновление аналитики ожидается.')}

    def mark(self, version):
        if type(version) is not int or not 1 <= version <= MAX_VERSION:
            raise ValueError('invalid marker version')
        if not self.deleted:
            self.marker = max(self.marker, version)

    def restore(self, payment):
        """Existing asynchronous DELETE; no new marker-delivery protocol."""
        if self.deleted:
            raise LookupError('deleted user')
        self.assignments[payment] = (False, 0)
        self.calculation_pending = True

    def ack_marker(self, version):
        self.mark(version)
        self.jobs.discard(version)  # ACK ends this job; calculation has not run.

    def apply_state(self, version, recalculating):
        if type(version) is not int or not 1 <= version <= MAX_VERSION:
            raise ValueError('invalid state version')
        if self.deleted or version < self.state_version:
            return
        if version == self.state_version and recalculating != self.stored_flag:
            raise ValueError('same state version must have identical content')
        self.state_version, self.stored_flag = version, recalculating
        if not recalculating:
            self.ready_version = max(self.ready_version, version)

    def compose_dismissal(self, target, compose):
        """One local marker+composition transaction; no financial overlay or source reads."""
        if self.deleted:
            raise LookupError('deleted user')
        if type(target) is not int or not 1 <= target <= MAX_VERSION:
            raise ValueError('invalid target')
        before = self.marker
        try:
            self.mark(target)
            return compose()
        except Exception:
            self.marker = before
            raise

    def start(self, user_action=True):
        if self.current > MAX_VERSION - 3:
            raise OverflowError('calculation version exhausted')
        self.current += 1
        self.apply_state(self.current, user_action)

    def finish(self, user_action=True):
        # Models the existing immutable chunks + final reservation under the user lock.
        if self.current > MAX_VERSION - 2:
            raise OverflowError('calculation version exhausted')
        self.current += 2
        self.apply_state(self.current - 1, user_action)
        if self.on_publish is not None:
            self.on_publish(self.current)
        self.apply_state(self.current, False)
        self.calculation_pending = False

    def run_pending(self):
        """Independent durable calculation queue, separate from marker delivery/Apply."""
        if self.calculation_pending and not self.deleted:
            self.start()
            self.finish()

    def calculate_now(self):
        self.start(False)
        self.finish(False)

    def state(self):
        return {'calc_version': self.state_version,
                'recalculating': self.stored_flag or self.marker > self.state_version}

    def purge(self):
        self.deleted = True
        self.current = self.state_version = self.marker = 0
        self.stored_flag = False
        self.ready_version = 0
        self.assignments.clear()
        self.jobs.clear()
        self.calculation_pending = False
