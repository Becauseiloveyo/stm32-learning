"""T11 P2 host-only, single-writer persistent snapshot transport."""

import json
import math
import re
from research.notification_t07.storage import Store, ContractError, encode, digest

VERSION = 't11-p2-v1'
SCHEMA = '''CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE records (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL);
'''


def _time(n):
    if type(n) is not int or not 0 <= n <= 1000000000:
        raise ContractError('invalid simulated time')


def _evidence_time(n):
    if (
        isinstance(n, bool)
        or not isinstance(n, (int, float))
        or not math.isfinite(n)
        or not 0 <= n <= 1000000000
    ):
        raise ContractError('invalid evidence time')


def _snapshot(s):
    if (
        not isinstance(s, dict)
        or s.get('version') != 't11-p1-v1'
        or s.get('label') != 'SYNTHETIC_UNCALIBRATED'
        or s.get('collection_authorized') is not False
    ):
        raise ContractError('invalid P1 snapshot')
    _time(s.get('now_s'))
    if type(s.get('sequence')) is not int or not 1 <= s['sequence'] <= 2**53:
        raise ContractError('invalid sequence')
    d = s.get('domain')
    if (
        not isinstance(d, dict)
        or set(d) != {'device', 'boot', 'batch', 'installation'}
        or any(not isinstance(v, str) or not v or len(v) > 80 for v in d.values())
    ):
        raise ContractError('invalid domain')
    if not isinstance(s.get('intents'), list):
        raise ContractError('invalid intents')
    for i in s['intents']:
        if (
            not isinstance(i, dict)
            or set(i)
            != {'kind', 'priority', 'key', 'evidence_s', 'expires_s', 'invalidation_reason'}
            or i['kind']
            not in (
                'local_wet',
                'forecast_risk',
                'prediction_conditional',
                'prediction_revoked',
                'prediction_unresolved',
                'prediction_coverage_or_model_mismatch',
            )
            or not isinstance(i['key'], str)
        ):
            raise ContractError('invalid intent')
        _evidence_time(i['evidence_s'])
        _evidence_time(i['expires_s'])
        if i['evidence_s'] > s['now_s'] or i['expires_s'] <= i['evidence_s']:
            raise ContractError('invalid intent times')
    encode(s)


class Base:

    def __init__(self, path, create=False):
        self.store = Store(path, create=create, schema=SCHEMA)
        row = self.store.db.execute("SELECT v FROM meta WHERE k='role'").fetchone()
        if create:
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO meta VALUES('role',?)", (self.__class__.__name__,)
                )
        elif row is None or row[0] != self.__class__.__name__:
            self.store.close()
            raise ContractError('wrong store role')

    def close(self):
        self.store.close()

    def _get(self, k):
        r = self.store.db.execute('SELECT v FROM meta WHERE k=?', (k,)).fetchone()
        return r[0] if r else None

    def _set(self, k, v):
        self.store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, str(v)))

    def _head(self):
        r = self.store.db.execute('SELECT body FROM head WHERE singleton=1').fetchone()
        return json.loads(r[0]) if r else None

    def _puthead(self, h):
        self.store.db.execute('INSERT OR REPLACE INTO head VALUES(1,?)', (encode(h),))

    def _clock(self, now_s):
        old = self._get('clock')
        if old is not None and now_s < int(old):
            raise ContractError('clock rollback')
        self._set('clock', now_s)

    def _bind(self, device):
        prior = self._get('device')
        if prior is not None and prior != device:
            raise ContractError('device mismatch')
        self._set('device', device)


class Outbox(Base):

    def __init__(self, path, create=False, capacity=32):
        if type(capacity) is not int or capacity < 1:
            raise ContractError('invalid capacity')
        super().__init__(path, create)
        with self.store.transaction():
            if create:
                self._set('capacity', capacity)
            elif int(self._get('capacity')) != capacity:
                raise ContractError('capacity mismatch')
        self.capacity = capacity

    @property
    def dropped_history_count(self):
        return int(self._get('dropped_history_count') or 0)

    def capture(self, snapshot):
        _snapshot(snapshot)
        with self.store.transaction():
            self._bind(snapshot['domain']['device'])
            prev = self._head()
            if prev and (
                snapshot['sequence'] < prev['snapshot']['sequence']
                or snapshot['now_s'] < prev['snapshot']['now_s']
            ):
                raise ContractError('out of order capture')
            if (
                prev
                and snapshot['sequence'] == prev['snapshot']['sequence']
                and snapshot['now_s'] == prev['snapshot']['now_s']
            ):
                if encode(snapshot) != encode(prev['snapshot']):
                    raise ContractError('conflicting capture')
                return
            revision = int(self._get('revision') or 0) + 1
            h = {'version': VERSION, 'revision': revision, 'snapshot': snapshot}
            self._puthead(h)
            self._set('revision', revision)
            body = encode(h)
            ident = digest(h)
            self.store.db.execute('INSERT INTO records VALUES(?,?)', (ident, body))
            # Eviction and its count commit in the same transaction as the head.
            removed = self.store.db.execute(
                'DELETE FROM records WHERE rowid IN '
                '(SELECT rowid FROM records ORDER BY rowid DESC LIMIT -1 OFFSET ?)',
                (self.capacity,),
            ).rowcount
            if removed:
                self._set('dropped_history_count', self.dropped_history_count + removed)

    def envelope(self):
        h = self._head()
        if h is None:
            return None
        row = self.store.db.execute('SELECT id,body FROM records ORDER BY rowid LIMIT 1').fetchone()
        return {
            'version': VERSION,
            'head': h,
            'record': json.loads(row['body']) if row else None,
            'record_id': row['id'] if row else None,
        }

    def acknowledge(self, ack):
        if (
            not isinstance(ack, dict)
            or set(ack) != {'version', 'device', 'record_id'}
            or ack['version'] != VERSION
            or not isinstance(ack['device'], str)
            or ack['device'] != self._get('device')
            or (
                ack['record_id'] is not None
                and (
                    not isinstance(ack['record_id'], str)
                    or re.fullmatch(r'[0-9a-f]{64}', ack['record_id']) is None
                )
            )
        ):
            raise ContractError('invalid ACK')
        ident = ack.get('record_id')
        if ident is not None:
            with self.store.transaction():
                self.store.db.execute('DELETE FROM records WHERE id=?', (ident,))


def _headcheck(h):
    if (
        not isinstance(h, dict)
        or set(h) != {'version', 'revision', 'snapshot'}
        or h['version'] != VERSION
        or type(h['revision']) is not int
        or h['revision'] < 1
    ):
        raise ContractError('invalid head')
    _snapshot(h['snapshot'])


class Service(Base):

    def submit(self, envelope, now_s):
        _time(now_s)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {'version', 'head', 'record', 'record_id'}
            or envelope['version'] != VERSION
        ):
            raise ContractError('invalid envelope')
        h = envelope['head']
        _headcheck(h)
        rec = envelope['record']
        ident = envelope['record_id']
        if rec is None:
            if ident is not None:
                raise ContractError('invalid record')
        else:
            _headcheck(rec)
            if (
                ident != digest(rec)
                or rec['revision'] > h['revision']
                or rec['snapshot']['domain']['device'] != h['snapshot']['domain']['device']
            ):
                raise ContractError('invalid record')
        if h['snapshot']['now_s'] > now_s or (rec is not None and rec['snapshot']['now_s'] > now_s):
            raise ContractError('future head or record')
        with self.store.transaction():
            self._clock(now_s)
            self._bind(h['snapshot']['domain']['device'])
            for item in (h, rec):
                if item is not None:
                    k = 'revision:' + str(item['revision'])
                    value = digest(item)
                    previous = self._get(k)
                    if previous is not None and previous != value:
                        raise ContractError('revision conflict')
                    self._set(k, value)
            old = self._head()
            if old and old['revision'] == h['revision'] and (encode(old) != encode(h)):
                raise ContractError('conflicting head')
            if old is None or h['revision'] > old['revision']:
                self._puthead(h)
            if rec is not None:
                self.store.db.execute(
                    'INSERT OR IGNORE INTO records VALUES(?,?)', (ident, encode(rec))
                )
        return {'version': VERSION, 'device': h['snapshot']['domain']['device'], 'record_id': ident}

    def dispatch(self, receiver, now_s):
        _time(now_s)
        h = self._head()
        if h is None:
            return []
        with self.store.transaction():
            self._clock(now_s)
        return receiver.receive(h, now_s)


class Receiver(Base):

    def receive(self, h, now_s):
        _time(now_s)
        _headcheck(h)
        if h['snapshot']['now_s'] > now_s:
            raise ContractError('future head')
        with self.store.transaction():
            self._clock(now_s)
            self._bind(h['snapshot']['domain']['device'])
            old = self._head()
            if old and old['revision'] == h['revision'] and (encode(old) != encode(h)):
                raise ContractError('conflicting head')
            if old is None or h['revision'] > old['revision']:
                self._puthead(h)
            else:
                h = old
            visible = [
                i
                for i in h['snapshot']['intents']
                if i['kind'] != 'prediction_revoked' and now_s < i['expires_s']
            ]
            self.notifications = []
            for i in visible:
                key = 'notice:' + i['key']
                if self._get(key) is None:
                    self._set(key, '1')
                    self.notifications.append(i)
            return visible
