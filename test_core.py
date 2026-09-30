import os
import io
import json
import unittest
from datetime import datetime, timedelta, timezone

import db
import collector
import pandas as pd

TEST_DB = "test_app.db"


class CoreLogicTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        db.init_db(TEST_DB)

    @classmethod
    def tearDownClass(cls):
        for f in [TEST_DB, f"{TEST_DB}-wal", f"{TEST_DB}-shm"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def setUp(self):
        with db.get_connection(TEST_DB) as conn:
            conn.execute("DELETE FROM oauth_tokens;")
            conn.execute("DELETE FROM subscriptions_master;")
            conn.execute("DELETE FROM app_settings;")
            conn.execute("DELETE FROM quota_usage;")
            conn.commit()

    def test_database_token_crud(self):
        # 1. Upsert token
        now = datetime.now(timezone.utc)
        db.upsert_token(
            channel_id="UC_test123",
            email="user@test.com",
            title="My Test Channel",
            refresh_token="ref_token_secret_123",
            access_token="acc_token_456",
            expiry=now,
            db_file=TEST_DB,
        )

        tokens = db.get_all_tokens(TEST_DB)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["channel_id"], "UC_test123")
        self.assertEqual(tokens[0]["channel_title"], "My Test Channel")
        self.assertEqual(tokens[0]["refresh_token"], "ref_token_secret_123")

        # 2. Update token
        db.upsert_token(
            channel_id="UC_test123",
            email="user@test.com",
            title="My Updated Title",
            refresh_token="ref_token_updated",
            db_file=TEST_DB,
        )
        tokens_after = db.get_all_tokens(TEST_DB)
        self.assertEqual(len(tokens_after), 1)
        self.assertEqual(tokens_after[0]["channel_title"], "My Updated Title")
        self.assertEqual(tokens_after[0]["refresh_token"], "ref_token_updated")

        # 3. Delete token
        db.delete_token("UC_test123", db_file=TEST_DB)
        tokens_empty = db.get_all_tokens(TEST_DB)
        self.assertEqual(len(tokens_empty), 0)

    def test_app_settings(self):
        # Initial should be default
        val = db.get_setting("client_id", default="default_id", db_file=TEST_DB)
        self.assertEqual(val, "default_id")

        # Set and get
        db.set_setting("client_id", "my_google_client_id", db_file=TEST_DB)
        db.set_setting("client_secret", "my_google_client_secret", db_file=TEST_DB)

        self.assertEqual(db.get_setting("client_id", db_file=TEST_DB), "my_google_client_id")
        self.assertEqual(db.get_setting("client_secret", db_file=TEST_DB), "my_google_client_secret")

    def test_quota_tracking(self):
        initial_quota = db.get_today_quota(TEST_DB)
        self.assertEqual(initial_quota, 0)

        db.log_quota(1, "subscriptions.list", db_file=TEST_DB)
        db.log_quota(5, "channels.list", db_file=TEST_DB)
        db.log_quota(10, "playlistItems.list", db_file=TEST_DB)

        total_quota = db.get_today_quota(TEST_DB)
        self.assertEqual(total_quota, 16)

    def test_liveness_calculation(self):
        self.assertEqual(collector.calculate_liveness(days_since=0, has_videos=True), "GREEN")
        self.assertEqual(collector.calculate_liveness(days_since=90, has_videos=True), "GREEN")
        self.assertEqual(collector.calculate_liveness(days_since=91, has_videos=True), "YELLOW")
        self.assertEqual(collector.calculate_liveness(days_since=180, has_videos=True), "YELLOW")
        self.assertEqual(collector.calculate_liveness(days_since=181, has_videos=True), "RED")
        self.assertEqual(collector.calculate_liveness(days_since=500, has_videos=True), "RED")
        self.assertEqual(collector.calculate_liveness(days_since=10, has_videos=False), "UNKNOWN")
        self.assertEqual(collector.calculate_liveness(days_since=None, has_videos=True), "UNKNOWN")

    def test_subscription_record_upsert_and_fetch(self):
        record = {
            "channel_id": "UC_sub_target",
            "title": "Awesome Tech Channel",
            "description": "Tech tutorials and reviews",
            "custom_url": "@awesometech",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "categories": ["Technology", "Programming"],
            "uploads_playlist_id": "UU_sub_target",
            "last_upload_at": "2026-09-20 12:00:00",
            "days_since_last_upload": 10,
            "liveness_status": "GREEN",
            "source_accounts": ["Main Account", "Sub Dev"],
            "recent_videos": [
                {
                    "video_id": "vid1",
                    "title": "Intro to Python 3.12",
                    "video_url": "https://www.youtube.com/watch?v=vid1",
                    "thumbnail_url": "https://example.com/vid1.jpg",
                    "published_at": "2026-09-20T12:00:00Z"
                }
            ],
            "raw_subscription_json": {"id": "sub_item_raw"},
            "raw_channel_json": {"id": "ch_item_raw"},
        }

        db.upsert_subscription_record(record, db_file=TEST_DB)
        records = db.fetch_master_records(TEST_DB)
        self.assertEqual(len(records), 1)

        row = records[0]
        self.assertEqual(row["channel_id"], "UC_sub_target")
        self.assertEqual(row["title"], "Awesome Tech Channel")
        self.assertEqual(row["liveness_status"], "GREEN")
        self.assertEqual(row["days_since_last_upload"], 10)

        # JSON deserialization checks
        cats = json.loads(row["categories"])
        self.assertIn("Technology", cats)
        sources = json.loads(row["source_accounts"])
        self.assertEqual(len(sources), 2)
        vids = json.loads(row["recent_videos"])
        self.assertEqual(len(vids), 1)
        self.assertEqual(vids[0]["video_id"], "vid1")

    def test_excel_export_generation(self):
        # Verify creating pandas DataFrame and exporting to openpyxl BytesIO
        record = {
            "channel_id": "UC_sub_excel",
            "title": "Excel Test Channel",
            "description": "Description line 1\nline 2",
            "custom_url": "@exceltest",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "categories": ["Finance"],
            "uploads_playlist_id": "UU_sub_excel",
            "last_upload_at": "2026-09-10 10:00:00",
            "days_since_last_upload": 20,
            "liveness_status": "GREEN",
            "source_accounts": ["Work Account"],
            "recent_videos": [{"title": "Video Title 1"}, {"title": "Video Title 2"}],
            "raw_subscription_json": {},
            "raw_channel_json": {},
        }
        db.upsert_subscription_record(record, db_file=TEST_DB)
        rows = db.fetch_master_records(TEST_DB)

        export_rows = []
        for r in rows:
            vids = json.loads(r["recent_videos"]) if r["recent_videos"] else []
            sources = json.loads(r["source_accounts"]) if r["source_accounts"] else []
            cats = json.loads(r["categories"]) if r["categories"] else []
            export_rows.append({
                "상태": r["liveness_status"],
                "채널명": r["title"],
                "채널홈URL": f"https://www.youtube.com/{r['custom_url']}" if r["custom_url"] else f"https://www.youtube.com/channel/{r['channel_id']}",
                "카테고리": ", ".join(cats),
                "최근 업로드일": r["last_upload_at"],
                "경과일수": r["days_since_last_upload"],
                "구독계정출처": ", ".join(sources),
                "최근영상1": vids[0]["title"] if len(vids) > 0 else "",
                "최근영상2": vids[1]["title"] if len(vids) > 1 else "",
                "최근영상3": vids[2]["title"] if len(vids) > 2 else "",
                "채널설명": (r["description"] or "").replace("\n", " "),
            })

        out_df = pd.DataFrame(export_rows)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Subscriptions_SSOT")

        data = buffer.getvalue()
        self.assertGreater(len(data), 1000)  # Check valid non-empty xlsx bytes generated


if __name__ == "__main__":
    unittest.main()
