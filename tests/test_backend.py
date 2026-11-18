# Backend integration tests: exercise Flask routes, SQLite persistence, imports, ranges, and job guards from backend.app and backend.jobs.

import io
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from backend.app import create_app
from backend import jobs, routes
from scripts.query_worksight import imbalances
from worksight import pipeline, storage
from worksight.csv_io import read_events, write_events_csv


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, 'DB_PATH', self.root / 'test.sqlite3'),
                        patch.object(routes, 'DATA_DIR', self.root),
                        patch.object(routes, 'UPLOADS_DIR', self.root / 'uploads')]
        for item in self.patches:
            item.start()
        self.app = create_app(testing=True)
        self.client = self.app.test_client()
        self.clip = self.root / 'test.mp4'
        writer = cv2.VideoWriter(str(self.clip), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
        for _ in range(20):
            writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
        writer.release()
        self.csv = b'Time,Track,Signal,Duration (s),Confidence\n00:00,7,candidate low activity,1,0.8\n'

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def import_pair(self, csv=None):
        return self.client.post('/api/imports', data={'video': (io.BytesIO(self.clip.read_bytes()), 'arbitrary.mp4'),
                    'csv': (io.BytesIO(self.csv if csv is None else csv), 'events.csv')})

    def test_upload_and_zone_validation(self):
        result = self.client.post('/api/videos', data={'video': (io.BytesIO(self.clip.read_bytes()), 'new.mp4')})
        self.assertEqual(result.status_code, 201)
        video_id = result.json['id']
        points = [[0, 0], [1, 0], [1, 1]]
        self.assertEqual(self.client.put(f'/api/videos/{video_id}/zones', json={'conveyor': points}).status_code, 400)
        self.assertEqual(self.client.get(f'/api/videos/{video_id}/zones').json, {})
        self.assertEqual(self.client.put(f'/api/videos/{video_id}/zones', json={'conveyor': points, 'work_area': points}).status_code, 200)
        invalid = {'conveyor': [[.1, .1], [.8, .1], [.8, .8]], 'work_area': [[2, 0], [1, 0], [1, 1]]}
        self.assertEqual(self.client.put(f'/api/videos/{video_id}/zones', json=invalid).status_code, 400)
        self.assertEqual(self.client.get(f'/api/videos/{video_id}/zones').json, {'conveyor': points, 'work_area': points})
        preview = self.client.get(f'/api/videos/{video_id}/frame')
        self.assertEqual(preview.mimetype, 'image/jpeg')
        preview.close()

    def test_import_replacement_and_timestamp_precision(self):
        first = self.import_pair()
        second = self.import_pair(b'Start seconds,Track,Signal,Duration (s),Confidence\n0.25,8,work area presence,0.5,0.9\n')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json['id'], second.json['id'])
        self.assertEqual(second.json['events'][0]['seconds'], .25)
        self.assertEqual(len(second.json['events']), 1)
        self.assertEqual(self.client.get(f"/api/imports/{first.json['id']}").json['events'][0]['track'], '7')

    def test_empty_csv_valid_and_invalid_timestamps_rejected(self):
        self.assertEqual(self.import_pair(b'Time,Track,Signal,Duration (s),Confidence\n').status_code, 201)
        self.assertEqual(self.import_pair(b'Time,Track,Signal,Duration (s),Confidence\n00:10,7,test,1,0.9\n').status_code, 400)
        self.assertEqual(self.import_pair(b'Time,Track,Signal,Duration (s),Confidence\nnan,7,test,1,0.9\n').status_code, 400)

    def test_video_ranges_and_downloads(self):
        imported = self.import_pair().json
        with self.client.get(imported['video_url'], headers={'Range': 'bytes=0-15'}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(len(response.data), 16)
            self.assertEqual(response.data, self.clip.read_bytes()[:16])
        with self.client.get(imported['csv_url']) as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_empty_and_populated_csv_matches_database(self):
        storage.ensure_video('v', 'test.mp4', self.clip)
        target = self.root / 'export.csv'
        with storage.connect_db() as db:
            write_events_csv(db, 'v', target)
            self.assertEqual(read_events(target), [])
            db.execute("INSERT INTO events VALUES (1,'v',7,'candidate_low_activity',125,1025,1000,1,.9,'{}')")
            write_events_csv(db, 'v', target)
            event = read_events(target)[0]
            self.assertEqual(event['seconds'], .125)
            self.assertEqual(event['duration'], 1)
            self.assertEqual(event['run_id'], 'v')

    def test_run_history_and_single_job(self):
        storage.ensure_video('source', 'test.mp4', self.clip)
        points = [[0, 0], [1, 0], [1, 1]]
        storage.save_zones('source', {'conveyor': points, 'work_area': points})
        with patch('backend.jobs.subprocess.Popen') as popen, patch('backend.jobs.threading.Thread.start'), patch('backend.jobs.DATA_DIR', self.root):
            popen.return_value.poll.return_value = None
            first = self.client.post('/api/videos/source/runs', json={'analysis_fps': 5})
            self.assertEqual(first.status_code, 202)
            self.assertEqual(self.client.get(f"/api/runs/{first.json['id']}").json['status'], 'queued')
            self.assertEqual(self.client.post('/api/videos/source/runs', json={'analysis_fps': 5}).status_code, 400)
            popen.return_value.poll.return_value = 0
            second = self.client.post('/api/videos/source/runs', json={'analysis_fps': 5})
            self.assertNotEqual(first.json['id'], second.json['id'])
            with storage.connect_db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 2)
                jobs.update_run(db, first.json['id'], status='processing')
                jobs.update_run(db, second.json['id'], status='processing')
            popen.return_value.poll.return_value = None
            self.assertEqual(self.client.post(f"/api/runs/{first.json['id']}/pause").status_code, 400)
            popen.return_value.send_signal.assert_not_called()
            path = f"/api/runs/{second.json['id']}"
            self.assertEqual(self.client.post(path + '/pause').json['status'], 'paused')
            popen.return_value.send_signal.assert_called_once_with(signal.SIGSTOP)
            self.assertEqual(self.client.post(path + '/resume').json['status'], 'processing')
            self.assertEqual(self.client.post(path + '/pause').json['status'], 'paused')
            self.assertEqual(self.client.post(path + '/cancel').json['status'], 'cancelled')
            self.assertEqual(storage.get_video(second.json['id'])['status'], 'cancelled')

    def test_staffing_events_and_queries_share_fingertip_evidence(self):
        storage.ensure_video('v', 'test.mp4', self.clip)
        with storage.connect_db() as db:
            for frame in range(10):
                for track in (1, 2, 3):
                    db.execute(
                        "INSERT INTO detections VALUES (NULL,'v',?,?,?,.9,'[]','[]',1,?)",
                        (frame, frame * 100, track, int(track == 1)),
                    )
                db.execute(
                    "INSERT INTO hand_detections VALUES (NULL,'v',?,?,2,'left',.9,'[]',1)",
                    (frame, frame * 100),
                )
            pipeline._record_staffing_mismatch_events(db, 'v', 100)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(imbalances(db, storage.get_video('v'), min_frames=1)['windows'], [])
            db.execute("DELETE FROM hand_detections")
            pipeline._record_staffing_mismatch_events(db, 'v', 100)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(imbalances(db, storage.get_video('v'), min_frames=1)['windows'][0]['qualifying_frames'], 10)

    def test_unknown_video(self):
        self.assertEqual(self.client.get('/api/videos/missing/zones').status_code, 404)
        self.assertEqual(self.client.get('/api/imports/' + '0' * 32).status_code, 404)

    def test_failed_model_preflight_preserves_prior_evidence(self):
        storage.ensure_video('old', 'test.mp4', self.clip)
        with storage.connect_db() as db:
            db.execute("INSERT INTO events VALUES (1,'old',7,'candidate_low_activity',0,900,1000,0,.9,'{}')")
        points = [[0, 0], [1, 0], [1, 1]]
        with patch.object(pipeline, 'load_models', side_effect=RuntimeError('Missing mandatory model')):
            with self.assertRaisesRegex(RuntimeError, 'Missing mandatory model'):
                pipeline.process_video('old', self.clip, {'conveyor': points, 'work_area': points})
        with storage.connect_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE video_id='old'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
