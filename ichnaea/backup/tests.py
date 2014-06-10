import boto
from contextlib import contextmanager
from mock import MagicMock, patch
import datetime
import hashlib
import pytz

from zipfile import ZipFile

from ichnaea.backup.s3 import S3Backend
from ichnaea.backup.tasks import (
    delete_cellmeasure_records,
    delete_wifimeasure_records,
    schedule_cellmeasure_archival,
    schedule_wifimeasure_archival,
    write_cellmeasure_s3_backups,
    write_wifimeasure_s3_backups,
)
from ichnaea.models import (
    CellMeasure,
    MeasureBlock,
    MEASURE_TYPE_CODE,
    WifiMeasure,
)
from ichnaea.tests.base import CeleryTestCase


@contextmanager
def mock_s3():
    mock_conn = MagicMock()
    mock_key = MagicMock()
    with patch.object(boto, 'connect_s3', mock_conn):
        with patch('boto.s3.key.Key', lambda _: mock_key):
            yield mock_key


class TestBackup(CeleryTestCase):

    def test_backup(self):
        prefix = 'backups/tests'
        with mock_s3() as mock_key:
            s3 = S3Backend('localhost.bucket', prefix, self.heka_client)
            s3.backup_archive('some_key', '/tmp/not_a_real_file.zip')
            self.assertEquals(mock_key.key, '/'.join([prefix, 'some_key']))
            method = mock_key.set_contents_from_filename
            self.assertEquals(method.call_args[0][0],
                              '/tmp/not_a_real_file.zip')


class TestMeasurementsDump(CeleryTestCase):

    def setUp(self):
        CeleryTestCase.setUp(self)
        self.really_old = datetime.datetime(1980, 1, 1).replace(
            tzinfo=pytz.UTC)

    def test_schedule_cell_measures(self):
        session = self.db_master_session
        measures = []
        for i in range(20):
            measures.append(CellMeasure(created=self.really_old))
        session.add_all(measures)
        session.flush()
        start_id = measures[0].id

        blocks = schedule_cellmeasure_archival(batch=15)
        self.assertEquals(len(blocks), 1)
        block = blocks[0]
        self.assertEquals(block, (start_id, start_id + 15))

        blocks = schedule_cellmeasure_archival(batch=6)
        self.assertEquals(len(blocks), 0)

        blocks = schedule_cellmeasure_archival(batch=5)
        self.assertEquals(len(blocks), 1)
        block = blocks[0]
        self.assertEquals(block, (start_id + 15, start_id + 20))

        blocks = schedule_cellmeasure_archival(batch=1)
        self.assertEquals(len(blocks), 0)

    def test_schedule_wifi_measures(self):
        session = self.db_master_session
        batch_size = 10
        measures = []
        for i in range(batch_size * 2):
            measures.append(WifiMeasure(created=self.really_old))
        session.add_all(measures)
        session.flush()
        start_id = measures[0].id

        blocks = schedule_wifimeasure_archival(batch=batch_size)
        self.assertEquals(len(blocks), 2)
        block = blocks[0]
        self.assertEquals(block,
                          (start_id, start_id + batch_size))

        block = blocks[1]
        self.assertEquals(block,
                          (start_id + batch_size, start_id + 2 * batch_size))

        blocks = schedule_wifimeasure_archival(batch=batch_size)
        self.assertEquals(len(blocks), 0)

    def test_backup_cell_to_s3(self):
        session = self.db_master_session
        batch_size = 10
        measures = []
        for i in range(batch_size):
            measures.append(CellMeasure(created=self.really_old))
        session.add_all(measures)
        session.flush()
        start_id = measures[0].id

        blocks = schedule_cellmeasure_archival(batch=batch_size)
        self.assertEquals(len(blocks), 1)
        block = blocks[0]
        self.assertEquals(block, (start_id, start_id + batch_size))

        with mock_s3():
            with patch.object(S3Backend,
                              'backup_archive', lambda x, y, z: True):
                write_cellmeasure_s3_backups(cleanup_zip=False)

                msgs = self.heka_client.stream.msgs
                info_msgs = [m for m in msgs if m.type == 'oldstyle']
                self.assertEquals(1, len(info_msgs))
                info = info_msgs[0]
                fname = info.payload.split(":")[-1]

                myzip = ZipFile(fname)
                try:
                    contents = set(myzip.namelist())
                    expected_contents = set(['alembic_revision.txt',
                                             'cell_measure.csv'])
                    self.assertEquals(expected_contents, contents)
                finally:
                    myzip.close()

        blocks = session.query(MeasureBlock).all()

        self.assertEquals(len(blocks), 1)
        block = blocks[0]

        actual_sha = hashlib.sha1()
        actual_sha.update(open(fname, 'rb').read())
        self.assertEquals(block.archive_sha, actual_sha.digest())
        self.assertTrue(block.s3_key is not None)
        self.assertTrue('/cell_' in block.s3_key)
        self.assertTrue(block.archive_date is None)

    def test_backup_wifi_to_s3(self):
        session = self.db_master_session
        batch_size = 10
        measures = []
        for i in range(batch_size):
            measures.append(WifiMeasure(created=self.really_old))
        session.add_all(measures)
        session.flush()
        start_id = measures[0].id

        blocks = schedule_wifimeasure_archival(batch=batch_size)
        self.assertEquals(len(blocks), 1)
        block = blocks[0]
        self.assertEquals(block, (start_id, start_id + batch_size))

        with mock_s3():
            with patch.object(S3Backend,
                              'backup_archive', lambda x, y, z: True):
                write_wifimeasure_s3_backups(cleanup_zip=False)

                msgs = self.heka_client.stream.msgs
                info_msgs = [m for m in msgs if m.type == 'oldstyle']
                self.assertEquals(1, len(info_msgs))
                info = info_msgs[0]
                fname = info.payload.split(":")[-1]

                myzip = ZipFile(fname)
                try:
                    contents = set(myzip.namelist())
                    expected_contents = set(['alembic_revision.txt',
                                             'wifi_measure.csv'])
                    self.assertEquals(expected_contents, contents)
                finally:
                    myzip.close()

        blocks = session.query(MeasureBlock).all()

        self.assertEquals(len(blocks), 1)
        block = blocks[0]

        actual_sha = hashlib.sha1()
        actual_sha.update(open(fname, 'rb').read())
        self.assertEquals(block.archive_sha, actual_sha.digest())
        self.assertTrue(block.s3_key is not None)
        self.assertTrue('/wifi_' in block.s3_key)
        self.assertTrue(block.archive_date is None)

    def test_delete_cell_measures(self):
        session = self.db_master_session
        block = MeasureBlock()
        block.measure_type = MEASURE_TYPE_CODE['cell']
        block.start_id = 120
        block.end_id = 140
        block.s3_key = 'fake_key'
        block.archive_sha = 'fake_sha'
        block.archive_date = None
        session.add(block)

        for i in range(100, 150):
            session.add(CellMeasure(id=i, created=self.really_old))
        session.commit()

        with patch.object(S3Backend, 'check_archive', lambda x, y, z: True):
            delete_cellmeasure_records()

        self.assertEquals(session.query(CellMeasure).count(), 29)
        self.assertTrue(block.archive_date is not None)

    def test_delete_wifi_measures(self):
        session = self.db_master_session
        block = MeasureBlock()
        block.measure_type = MEASURE_TYPE_CODE['wifi']
        block.start_id = 120
        block.end_id = 140
        block.s3_key = 'fake_key'
        block.archive_sha = 'fake_sha'
        block.archive_date = None
        session.add(block)

        for i in range(100, 150):
            session.add(WifiMeasure(id=i, created=self.really_old))
        session.commit()

        with patch.object(S3Backend, 'check_archive', lambda x, y, z: True):
            delete_wifimeasure_records()

        self.assertEquals(session.query(WifiMeasure).count(), 29)
        self.assertTrue(block.archive_date is not None)

    def test_skip_delete_new_blocks(self):
        now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
        session = self.db_master_session
        block = MeasureBlock()
        block.measure_type = MEASURE_TYPE_CODE['cell']
        block.start_id = 120
        block.end_id = 140
        block.s3_key = 'fake_key'
        block.archive_sha = 'fake_sha'
        block.archive_date = None
        session.add(block)

        measures = []
        for i in range(100, 150):
            measures.append(CellMeasure(id=i, created=now))
        session.add_all(measures)
        session.commit()

        with patch.object(S3Backend, 'check_archive', lambda x, y, z: True):
            delete_cellmeasure_records()

        self.assertEquals(session.query(CellMeasure).count(), 50)
        # The archive_date should *not* be set as we haven't deleted
        # the actual records yet
        self.assertTrue(block.archive_date is None)

        # Update all the create dates to be far in the past
        for m in measures:
            m.created = self.really_old
        session.add_all(measures)
        session.commit()

        with patch.object(S3Backend, 'check_archive', lambda x, y, z: True):
            delete_cellmeasure_records()

        self.assertEquals(session.query(CellMeasure).count(), 29)
        # The archive_date should now be set as the measure records
        # have been deleted.
        self.assertTrue(block.archive_date is not None)
