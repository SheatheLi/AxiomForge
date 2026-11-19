"""dbt Fusion #14525 source-side microbatch leakage, fixed control and safety gates."""
from __future__ import annotations
from pathlib import Path
import sqlite3
import pytest

from axiomforge.microbatch import inspect_microbatch,MicrobatchError

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'examples'/'external_issues'


def test_public_report_pattern_10_source_events_four_batches_40_reads():
    report=inspect_microbatch(FIX/'microbatch.sqlite',FIX/'microbatch_bug.sql',
         source='source',source_id='event_id',source_date='event_at',
         start='2020-01-01',end='2020-01-05')
    assert report['status']=='RISK'
    assert report['counts']['source_rows']==10
    assert report['counts']['total_batch_read_rows']==40
    assert report['counts']['cross_batch_duplicated_id_count']==10
    assert 'SOURCE_WINDOW_LEAK' in report['flags']
    assert 'MULTIBATCH_DUPLICATED' in report['flags']
    assert len(report['windows'])==4


def test_fixed_source_predicate_only_emits_current_batch():
    report=inspect_microbatch(FIX/'microbatch.sqlite',FIX/'microbatch_fixed.sql',
         source='source',source_id='event_id',source_date='event_at',
         start='2020-01-01',end='2020-01-05')
    assert report['status']=='PASS'
    assert report['counts']['total_batch_read_rows']==10
    assert report['counts']['cross_batch_duplicated_id_count']==0
    assert all(w['out_of_window_rows']==0 for w in report['windows'])


def test_missing_batch_rejected_not_silently_passed():
    report=inspect_microbatch(FIX/'microbatch.sqlite',
         'SELECT event_id,event_at FROM source WHERE event_at >= "2020-01-03"',
         source='source',source_id='event_id',source_date='event_at',
         start='2020-01-01',end='2020-01-05')
    assert 'WINDOW_SOURCE_MISSING' in report['flags']


def test_outside_range_event_rejected():
    report=inspect_microbatch(FIX/'microbatch.sqlite','SELECT event_id,event_at FROM source',
         source='source',source_id='event_id',source_date='event_at',
         start='2020-01-01',end='2020-01-03')
    assert 'OUTSIDE_RANGE_RESULT' in report['flags']


@pytest.mark.parametrize('sql',[
    'DELETE FROM source',
    'SELECT event_id,event_at FROM source; DROP TABLE source',
    'SELECT random() as event_id,event_at FROM source',
    'SELECT event_id FROM source',
])
def test_unsupported_or_unsafe_microbatch_sql(sql):
    with pytest.raises((MicrobatchError,ValueError)):
        inspect_microbatch(FIX/'microbatch.sqlite',sql,source='source',
          source_id='event_id',source_date='event_at',start='2020-01-01',end='2020-01-05')


def test_bounded_date_and_max_rows_rejected():
    with pytest.raises(MicrobatchError):
        inspect_microbatch(FIX/'microbatch.sqlite',FIX/'microbatch_fixed.sql',source='source',
          source_id='event_id',source_date='event_at',start='2020-01-01',end='2020-06-01')
    with pytest.raises(MicrobatchError):
        inspect_microbatch(FIX/'microbatch.sqlite',FIX/'microbatch_fixed.sql',source='source',
          source_id='event_id',source_date='event_at',start='2020-01-01',end='2020-01-05',max_rows=2)


def test_duplicate_original_identity_is_input_error(tmp_path):
    db=tmp_path/'bad.sqlite';c=sqlite3.connect(db)
    with c:
        c.executescript('CREATE TABLE source (event_id INTEGER,event_at TEXT);')
        c.executemany('INSERT INTO source VALUES (?,?)',[(1,'2020-01-01'),(1,'2020-01-02')])
    c.close()
    with pytest.raises(MicrobatchError):
        inspect_microbatch(db,'SELECT event_id,event_at FROM source',source='source',
            source_id='event_id',source_date='event_at',start='2020-01-01',end='2020-01-03')


def test_modified_timestamp_cannot_evade_window_oracle():
    # Wrong timestamp projected by a query must never be silently treated as
    # the genuine source event-time merely because it falls within batch bounds.
    report=inspect_microbatch(FIX/'microbatch.sqlite',
       "SELECT event_id, :end AS event_at FROM source WHERE event_at >= :start AND event_at < :end",
       source='source',source_id='event_id',source_date='event_at',
       start='2020-01-01',end='2020-01-05')
    assert report['status']=='RISK'
    assert 'SOURCE_CONTENT_MISMATCH' in report['flags']
