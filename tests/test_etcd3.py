"""
Tests for `etcd3` module.

----------------------------------
"""

import base64
import json
import os
import subprocess
import time

from hypothesis import given
from hypothesis.strategies import characters
import pytest
import six
from six.moves.urllib.parse import urlparse

import etcd3
import etcd3.etcdrpc as etcdrpc
import etcd3.utils as utils


etcd_version = os.environ.get('ETCD_VERSION', 'v3.0.10')

os.environ['ETCDCTL_API'] = '3'

if six.PY2:
    int_types = (int, long)
else:
    int_types = (int,)


def etcdctl(*args):
    endpoint = os.environ.get('ETCD_ENDPOINT', None)
    if endpoint:
        args = ['--endpoints', endpoint] + list(args)
    args = ['etcdctl', '-w', 'json'] + list(args)
    print(" ".join(args))
    output = subprocess.check_output(args)
    return json.loads(output.decode('utf-8'))


class TestEtcd3(object):

    @pytest.fixture
    def etcd(self):
        endpoint = os.environ.get('ETCD_ENDPOINT', None)
        if endpoint:
            url = urlparse(endpoint)
            yield etcd3.client(host=url.hostname, port=url.port)
        else:
            yield etcd3.client()

        # clean up after fixture goes out of scope
        etcdctl('del', '--prefix', '/')

    def test_get_unknown_key(self, etcd):
        with pytest.raises(etcd3.exceptions.KeyNotFoundError):
            etcd.get('probably-invalid-key')

    @given(characters(blacklist_categories=['Cs', 'Cc']))
    def test_get_key(self, etcd, string):
        etcdctl('put', '/doot/a_key', string)
        returned = etcd.get('/doot/a_key')
        assert returned == string.encode('utf-8')

    @given(characters(blacklist_categories=['Cs', 'Cc']))
    def test_get_random_key(self, etcd, string):
        etcdctl('put', '/doot/' + string, 'dootdoot')
        returned = etcd.get('/doot/' + string)
        assert returned == b'dootdoot'

    @given(characters(blacklist_categories=['Cs', 'Cc']))
    def test_put_key(self, etcd, string):
        etcd.put('/doot/put_1', string)
        out = etcdctl('get', '/doot/put_1')
        assert base64.b64decode(out['kvs'][0]['value']) == \
            string.encode('utf-8')

    def test_delete_key(self, etcd):
        etcdctl('put', '/doot/delete_this', 'delete pls')

        assert etcd.get('/doot/delete_this') == b'delete pls'

        etcd.delete('/doot/delete_this')

        with pytest.raises(etcd3.exceptions.KeyNotFoundError):
            etcd.get('/doot/delete_this')

    def test_transaction_success(self, etcd):
        etcdctl('put', '/doot/txn', 'dootdoot')
        etcd.transaction(
            compare=[etcd.transactions.value('/doot/txn') == 'dootdoot'],
            success=[etcd.transactions.put('/doot/txn', 'success')],
            failure=[etcd.transactions.put('/doot/txn', 'failure')]
        )
        out = etcdctl('get', '/doot/txn')
        assert base64.b64decode(out['kvs'][0]['value']) == b'success'

    def test_transaction_failure(self, etcd):
        etcdctl('put', '/doot/txn', 'notdootdoot')
        etcd.transaction(
            compare=[etcd.transactions.value('/doot/txn') == 'dootdoot'],
            success=[etcd.transactions.put('/doot/txn', 'success')],
            failure=[etcd.transactions.put('/doot/txn', 'failure')]
        )
        out = etcdctl('get', '/doot/txn')
        assert base64.b64decode(out['kvs'][0]['value']) == b'failure'

    def test_replace_success(self, etcd):
        etcd.put('/doot/thing', 'toot')
        status = etcd.replace('/doot/thing', 'toot', 'doot')
        assert etcd.get('/doot/thing') == b'doot'
        assert status is True

    def test_replace_fail(self, etcd):
        etcd.put('/doot/thing', 'boot')
        status = etcd.replace('/doot/thing', 'toot', 'doot')
        assert etcd.get('/doot/thing') == b'boot'
        assert status is False

    def test_get_prefix(self, etcd):
        for i in range(20):
            etcdctl('put', '/doot/range{}'.format(i), 'i am a range')

        for i in range(5):
            etcdctl('put', '/doot/notrange{}'.format(i), 'i am a not range')

        values = list(etcd.get_prefix('/doot/range'))
        assert len(values) == 20
        for key, value in values:
            assert value == b'i am a range'

    def test_all_not_found_error(self, etcd):
        with pytest.raises(etcd3.exceptions.KeyNotFoundError):
            list(etcd.get_all())

    def test_range_not_found_error(self, etcd):
        for i in range(5):
            etcdctl('put', '/doot/notrange{}'.format(i), 'i am a not range')

        with pytest.raises(etcd3.exceptions.KeyNotFoundError):
            list(etcd.get_prefix('/doot/range'))

    def test_get_all(self, etcd):
        for i in range(20):
            etcdctl('put', '/doot/range{}'.format(i), 'i am in all')

        for i in range(5):
            etcdctl('put', '/doot/notrange{}'.format(i), 'i am in all')
        values = list(etcd.get_all())
        assert len(values) == 25
        for key, value in values:
            assert value == b'i am in all'

    def test_sort_order(self, etcd):
        def remove_prefix(string, prefix):
            return string[len(prefix):]

        initial_keys = 'abcde'
        initial_values = 'qwert'

        for k, v in zip(initial_keys, initial_values):
            etcdctl('put', '/doot/{}'.format(k), v)

        keys = ''
        for key, value in etcd.get_prefix('/doot', sort_order='ascend'):
            keys += remove_prefix(key.decode('utf-8'), '/doot/')

        assert keys == initial_keys

        reverse_keys = ''
        for key, value in etcd.get_prefix('/doot', sort_order='descend'):
            reverse_keys += remove_prefix(key.decode('utf-8'), '/doot/')

        assert reverse_keys == ''.join(reversed(initial_keys))

    def test_lease_grant(self, etcd):
        lease = etcd.lease(1)

        assert isinstance(lease.ttl, int_types)
        assert isinstance(lease.id, int_types)

    def test_lease_revoke(self, etcd):
        lease = etcd.lease(1)
        lease.revoke()

    @pytest.mark.skipif(not etcd_version.startswith('v3.1'),
                        reason="requires etcd v3.1")
    def test_lease_keys_empty(self, etcd):
        lease = etcd.lease(1)
        assert lease.keys == []

    @pytest.mark.skipif(not etcd_version.startswith('v3.1'),
                        reason="requires etcd v3.1")
    def test_lease_single_key(self, etcd):
        lease = etcd.lease(1)
        etcd.put('/doot/lease_test', 'this is a lease', lease=lease)
        assert lease.keys == [b'/doot/lease_test']

    @pytest.mark.skipif(not etcd_version.startswith('v3.1'),
                        reason="requires etcd v3.1")
    def test_lease_expire(self, etcd):
        key = '/doot/lease_test_expire'
        lease = etcd.lease(1)
        etcd.put(key, 'this is a lease', lease=lease)
        assert lease.keys == [utils.to_bytes(key)]
        assert etcd.get(key) == b'this is a lease'

        # wait for the lease to expire
        time.sleep(lease.granted_ttl + 2)
        with pytest.raises(etcd3.exceptions.KeyNotFoundError):
            etcd.get(key)

    def test_member_list_single(self, etcd):
        # if tests are run against an etcd cluster rather than a single node,
        # this test will need to be changed
        assert len(list(etcd.members)) == 1
        for member in etcd.members:
            assert member.name == 'default'
            for peer_url in member.peer_urls:
                assert peer_url.startswith('http://')
            for client_url in member.client_urls:
                assert client_url.startswith('http://')
            assert isinstance(member.id, int_types) is True


class TestUtils(object):
    def test_increment_last_byte(self):
        assert etcd3.utils.increment_last_byte(b'foo') == b'fop'

    def test_to_bytes(self):
        assert isinstance(etcd3.utils.to_bytes(b'doot'), bytes) is True
        assert isinstance(etcd3.utils.to_bytes('doot'), bytes) is True
        assert etcd3.utils.to_bytes(b'doot') == b'doot'
        assert etcd3.utils.to_bytes('doot') == b'doot'


class TestClient(object):
    @pytest.fixture
    def etcd(self):
        yield etcd3.client()

    def test_sort_target(self, etcd):
        key = 'key'.encode('utf-8')
        sort_target = {
            None: etcdrpc.RangeRequest.KEY,
            'key': etcdrpc.RangeRequest.KEY,
            'version': etcdrpc.RangeRequest.VERSION,
            'create': etcdrpc.RangeRequest.CREATE,
            'mod': etcdrpc.RangeRequest.MOD,
            'value': etcdrpc.RangeRequest.VALUE,
        }

        for input, expected in sort_target.items():
            range_request = etcd._build_get_range_request(key,
                                                          sort_target=input)
            assert range_request.sort_target == expected
        with pytest.raises(ValueError):
            etcd._build_get_range_request(key, sort_target='feelsbadman')

    def test_sort_order(self, etcd):
        key = 'key'.encode('utf-8')
        sort_target = {
            None: etcdrpc.RangeRequest.NONE,
            'ascend': etcdrpc.RangeRequest.ASCEND,
            'descend': etcdrpc.RangeRequest.DESCEND,
        }

        for input, expected in sort_target.items():
            range_request = etcd._build_get_range_request(key,
                                                          sort_order=input)
            assert range_request.sort_order == expected
        with pytest.raises(ValueError):
            etcd._build_get_range_request(key, sort_order='feelsbadman')


class TestCompares(object):

    def test_compare_version(self):
        key = 'key'
        tx = etcd3.Transactions()

        version_compare = tx.version(key) == 1
        assert version_compare.op == etcdrpc.Compare.EQUAL

        version_compare = tx.version(key) < 91
        assert version_compare.op == etcdrpc.Compare.LESS

        version_compare = tx.version(key) > 92
        assert version_compare.op == etcdrpc.Compare.GREATER
        assert version_compare.build_message().target == \
            etcdrpc.Compare.VERSION

    def test_compare_value(self):
        key = 'key'
        tx = etcd3.Transactions()

        value_compare = tx.value(key) == 'b'
        assert value_compare.op == etcdrpc.Compare.EQUAL

        value_compare = tx.value(key) < 'b'
        assert value_compare.op == etcdrpc.Compare.LESS

        value_compare = tx.value(key) > 'b'
        assert value_compare.op == etcdrpc.Compare.GREATER
        assert value_compare.build_message().target == etcdrpc.Compare.VALUE

    def test_compare_mod(self):
        key = 'key'
        tx = etcd3.Transactions()

        mod_compare = tx.mod(key) == -100
        assert mod_compare.op == etcdrpc.Compare.EQUAL

        mod_compare = tx.mod(key) < 19
        assert mod_compare.op == etcdrpc.Compare.LESS

        mod_compare = tx.mod(key) > 21
        assert mod_compare.op == etcdrpc.Compare.GREATER
        assert mod_compare.build_message().target == etcdrpc.Compare.MOD

    def test_compare_create(self):
        key = 'key'
        tx = etcd3.Transactions()

        create_compare = tx.create(key) == 10
        assert create_compare.op == etcdrpc.Compare.EQUAL

        create_compare = tx.create(key) < 155
        assert create_compare.op == etcdrpc.Compare.LESS

        create_compare = tx.create(key) > -12
        assert create_compare.op == etcdrpc.Compare.GREATER
        assert create_compare.build_message().target == etcdrpc.Compare.CREATE
