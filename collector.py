import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import db

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def get_youtube_service(client_id: str, client_secret: str, refresh_token: str):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def calculate_liveness(days_since: Optional[int], has_videos: bool) -> str:
    """Calculate liveness status: GREEN (<=90d), YELLOW (91-180d), RED (>180d), UNKNOWN (0 videos)."""
    if not has_videos or days_since is None:
        return "UNKNOWN"
    if days_since <= 90:
        return "GREEN"
    if days_since <= 180:
        return "YELLOW"
    return "RED"


def fetch_all_subscriptions_for_token(youtube, source_channel_name: str) -> Dict[str, Dict[str, Any]]:
    channel_map: Dict[str, Dict[str, Any]] = {}
    next_page_token = None

    while True:
        try:
            res = youtube.subscriptions().list(
                part="snippet",
                mine=True,
                maxResults=50,
                pageToken=next_page_token,
            ).execute()
            db.log_quota(1, f"subscriptions.list for {source_channel_name}")
        except Exception as e:
            # Handle token expiration or permission error for this account
            raise RuntimeError(f"계정 '{source_channel_name}' 구독 목록 조회 실패: {str(e)}") from e

        for item in res.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            c_id = resource_id.get("channelId")
            if not c_id:
                continue

            if c_id not in channel_map:
                channel_map[c_id] = {
                    "channel_id": c_id,
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", ""),
                    "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", snippet.get("thumbnails", {}).get("default", {}).get("url", "")),
                    "source_accounts": [source_channel_name],
                    "raw_subscription_json": item,
                }
            else:
                if source_channel_name not in channel_map[c_id]["source_accounts"]:
                    channel_map[c_id]["source_accounts"].append(source_channel_name)

        next_page_token = res.get("nextPageToken")
        if not next_page_token:
            break

    return channel_map


def run_sync_pipeline(
    client_id: str,
    client_secret: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    max_channels: Optional[int] = None,
    target_channel_ids: Optional[List[str]] = None,
) -> int:
    """
    Executes the TubeSSOT synchronization pipeline:
    1. Collect subscriptions across selected connected accounts and deduplicate by channel_id.
    2. Batch fetch channel metadata via channels.list (chunks of 50).
    3. Query recent 5 videos via playlistItems.list and calculate liveness.
    4. Upsert all enriched records into SQLite subscriptions_master.
    If target_channel_ids is specified, only subscriptions belonging to those connected channels are retrieved.
    If max_channels is specified, only that many channels will be analyzed.
    Returns total unique channels processed.
    """
    def _notify(percent: float, message: str):
        if progress_callback:
            progress_callback(percent, message)

    tokens = db.get_all_tokens()
    if not tokens:
        raise ValueError("등록된 OAuth 계정/채널이 없습니다. 먼저 계정을 연동해 주세요.")

    if target_channel_ids:
        target_set = set(target_channel_ids)
        tokens = [t for t in tokens if t["channel_id"] in target_set]
        if not tokens:
            raise ValueError("선택된 연동 채널의 유효한 OAuth 토큰을 찾을 수 없습니다.")

    # Load existing subscriptions map to preserve known source accounts across partial syncs
    existing_records = db.fetch_master_records()
    db_source_map: Dict[str, Set[str]] = {}
    for er in existing_records:
        if er["source_accounts"]:
            try:
                db_source_map[er["channel_id"]] = set(json.loads(er["source_accounts"]))
            except Exception:
                db_source_map[er["channel_id"]] = set()

    merged_subscriptions: Dict[str, Dict[str, Any]] = {}
    valid_tokens = []

    # Step 1: Iterate over targeted tokens and collect subscriptions
    total_tokens = len(tokens)
    for idx, token in enumerate(tokens):
        ch_title = token["channel_title"]
        _notify(
            (idx / total_tokens) * 0.3,
            f"[{idx + 1}/{total_tokens}] '{ch_title}' 계정의 구독 목록을 수집하는 중...",
        )
        try:
            yt = get_youtube_service(client_id, client_secret, token["refresh_token"])
            subs = fetch_all_subscriptions_for_token(yt, ch_title)
            valid_tokens.append(token)
            for c_id, data in subs.items():
                if c_id in merged_subscriptions:
                    for src in data["source_accounts"]:
                        if src not in merged_subscriptions[c_id]["source_accounts"]:
                            merged_subscriptions[c_id]["source_accounts"].append(src)
                else:
                    # Merge with existing DB source accounts if present
                    if c_id in db_source_map:
                        for src in db_source_map[c_id]:
                            if src not in data["source_accounts"]:
                                data["source_accounts"].append(src)
                    merged_subscriptions[c_id] = data
        except Exception as e:
            _notify(
                (idx / total_tokens) * 0.3,
                f"경고: '{ch_title}' 수집 중 오류 발생 (건너뜀): {str(e)}",
            )
            continue

    unique_channel_ids = list(merged_subscriptions.keys())
    if max_channels and max_channels > 0:
        unique_channel_ids = unique_channel_ids[:max_channels]

    total_unique = len(unique_channel_ids)
    if total_unique == 0:
        _notify(1.0, "동기화 완료: 수집된 구독 채널이 없습니다.")
        return 0

    if not valid_tokens:
        raise RuntimeError("유효한 OAuth 토큰이 없습니다. 계정을 다시 연동해 주세요.")

    # Use first valid token for subsequent API calls
    yt_primary = get_youtube_service(client_id, client_secret, valid_tokens[0]["refresh_token"])

    # Step 2: Batch fetch channel details (50 per batch)
    _notify(0.32, f"총 {total_unique}개 고유 채널 메타데이터 배치 수집 중...")
    channels_meta: Dict[str, Dict[str, Any]] = {}
    chunk_size = 50

    for i in range(0, total_unique, chunk_size):
        chunk = unique_channel_ids[i:i + chunk_size]
        try:
            res = yt_primary.channels().list(
                part="snippet,topicDetails,contentDetails,statistics",
                id=",".join(chunk),
                maxResults=50,
            ).execute()
            db.log_quota(1, f"channels.list (batch {len(chunk)})")
            for ch_item in res.get("items", []):
                channels_meta[ch_item["id"]] = ch_item
        except HttpError as e:
            if e.resp.status == 403 and "quotaExceeded" in str(e):
                _notify(1.0, "API 일일 쿼터가 초과되어 수집이 중단되었습니다.")
                break
            _notify(0.35, f"배치 메타데이터 조회 중 일부 오류 발생: {str(e)}")
        except Exception as e:
            _notify(0.35, f"배치 메타데이터 조회 중 오류: {str(e)}")

    # Step 3: Check liveness for each channel
    now = datetime.now(timezone.utc)
    for idx, c_id in enumerate(unique_channel_ids):
        progress_val = 0.4 + (idx / total_unique) * 0.58
        sub_info = merged_subscriptions[c_id]
        ch_title = sub_info.get("title", c_id)
        _notify(progress_val, f"[{idx + 1}/{total_unique}] '{ch_title}' 활동성(Liveness) 분석 중...")

        ch_info = channels_meta.get(c_id, {})
        snippet = ch_info.get("snippet", {})
        custom_url = snippet.get("customUrl", "")
        topic_urls = ch_info.get("topicDetails", {}).get("topicCategories", [])
        categories = [url.split("/")[-1].replace("_", " ") for url in topic_urls]
        uploads_id = ch_info.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_id and c_id.startswith("UC"):
            uploads_id = "UU" + c_id[2:]

        recent_videos = []
        last_upload_at = None
        days_since = None
        liveness_status = "UNKNOWN"

        if uploads_id:
            try:
                p_res = yt_primary.playlistItems().list(
                    part="snippet",
                    playlistId=uploads_id,
                    maxResults=5,
                ).execute()
                db.log_quota(1, f"playlistItems.list for {c_id}")

                for item in p_res.get("items", []):
                    p_snippet = item.get("snippet", {})
                    v_id = p_snippet.get("resourceId", {}).get("videoId")
                    v_published = p_snippet.get("publishedAt")
                    v_title = p_snippet.get("title", "")
                    if v_id and v_title != "Private video" and v_title != "Deleted video":
                        recent_videos.append({
                            "video_id": v_id,
                            "title": v_title,
                            "video_url": f"https://www.youtube.com/watch?v={v_id}",
                            "thumbnail_url": p_snippet.get("thumbnails", {}).get("high", {}).get("url", p_snippet.get("thumbnails", {}).get("default", {}).get("url", "")),
                            "published_at": v_published,
                        })

                if recent_videos:
                    last_date_str = recent_videos[0]["published_at"]
                    # parse ISO timestamp
                    last_dt = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
                    last_upload_at = last_dt.strftime("%Y-%m-%d %H:%M:%S")
                    days_since = max(0, (now - last_dt).days)
                    liveness_status = calculate_liveness(days_since, has_videos=True)
                else:
                    liveness_status = "UNKNOWN"
            except HttpError as e:
                if e.resp.status == 403 and "quotaExceeded" in str(e):
                    _notify(1.0, "API 일일 쿼터가 초과되어 수집을 안전하게 마감합니다.")
                    break
                # Missing playlist, private, or channel removed
                liveness_status = "UNKNOWN"
            except Exception:
                liveness_status = "UNKNOWN"

            time.sleep(0.05)

        # Assemble record
        record = {
            "channel_id": c_id,
            "title": snippet.get("title", sub_info["title"]),
            "description": snippet.get("description", sub_info["description"]),
            "custom_url": custom_url,
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", sub_info["thumbnail_url"]),
            "categories": categories,
            "uploads_playlist_id": uploads_id or "",
            "last_upload_at": last_upload_at,
            "days_since_last_upload": days_since,
            "liveness_status": liveness_status,
            "source_accounts": sub_info["source_accounts"],
            "recent_videos": recent_videos,
            "raw_subscription_json": sub_info.get("raw_subscription_json", {}),
            "raw_channel_json": ch_info,
        }
        db.upsert_subscription_record(record)

    _notify(1.0, f"동기화 완료: 총 {total_unique}개 채널의 상태가 최신으로 동기화되었습니다.")
    return total_unique


def enrich_existing_channels_with_statistics(
    client_id: str,
    client_secret: str,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> int:
    """
    Backfills subscriberCount, videoCount, viewCount for channels in DB
    that do not yet have statistics, in batches of 50 (only 1 unit per 50 channels).
    """
    tokens = db.get_all_tokens()
    if not tokens:
        raise ValueError("등록된 연동 계정이 없습니다.")
    yt = get_youtube_service(client_id, client_secret, tokens[0]["refresh_token"])

    records = db.fetch_master_records()
    if not records:
        return 0

    target_channels = []
    for r in records:
        raw_ch = json.loads(r["raw_channel_json"]) if r["raw_channel_json"] else {}
        if "statistics" not in raw_ch:
            target_channels.append((r["channel_id"], raw_ch))

    total_targets = len(target_channels)
    if total_targets == 0:
        return 0

    chunk_size = 50
    updated_count = 0
    for i in range(0, total_targets, chunk_size):
        chunk = target_channels[i:i + chunk_size]
        chunk_ids = [c[0] for c in chunk]
        if progress_callback:
            progress_callback(i / total_targets, f"[{i + 1}/{total_targets}] 채널 구독자수/영상수 통계 보강 중...")
        try:
            res = yt.channels().list(
                part="statistics",
                id=",".join(chunk_ids),
                maxResults=50,
            ).execute()
            db.log_quota(1, f"channels.list statistics enrich ({len(chunk_ids)})")
            stats_map = {item["id"]: item.get("statistics", {}) for item in res.get("items", [])}

            with db.get_connection() as conn:
                for c_id, raw_ch in chunk:
                    if c_id in stats_map:
                        raw_ch["statistics"] = stats_map[c_id]
                        conn.execute("""
                        UPDATE subscriptions_master 
                        SET raw_channel_json = ?, updated_at = CURRENT_TIMESTAMP 
                        WHERE channel_id = ?
                        """, (json.dumps(raw_ch, ensure_ascii=False), c_id))
                        updated_count += 1
                conn.commit()
        except Exception as e:
            print(f"[Statistics Enrich Error] {str(e)}", flush=True)

    if progress_callback:
        progress_callback(1.0, f"통계 보강 완료! 총 {updated_count}개 채널의 구독자/영상수 반영됨")
    return updated_count
