import io
import json
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
import streamlit as st
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

import db
import collector

# Page setup
st.set_page_config(
    page_title="TubeSSOT - YouTube 구독 자산 통합 관리자",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize DB tables
db.init_db()

# --- SIDEBAR: Settings & Quota Tracking ---
st.sidebar.title("⚙️ 설정 및 API 상태")

# Preload settings from DB
saved_client_id = db.get_setting("google_client_id", "") or ""
saved_client_secret = db.get_setting("google_client_secret", "") or ""

with st.sidebar.expander("🔑 OAuth 클라이언트 자격증명", expanded=not bool(saved_client_id and saved_client_secret)):
    # File uploader for client_secret*.json
    uploaded_secret_file = st.file_uploader(
        "Google Cloud client_secret.json 업로드",
        type=["json"],
        help="Google Cloud Console에서 다운로드한 OAuth 클라이언트 JSON 파일을 업로드하면 ID와 Secret이 자동 입력됩니다.",
    )
    if uploaded_secret_file is not None:
        try:
            content = json.load(uploaded_secret_file)
            installed_data = content.get("installed") or content.get("web") or {}
            c_id = installed_data.get("client_id", "")
            c_secret = installed_data.get("client_secret", "")
            if c_id and c_secret:
                db.set_setting("google_client_id", c_id)
                db.set_setting("google_client_secret", c_secret)
                saved_client_id = c_id
                saved_client_secret = c_secret
                st.success("JSON 파일로부터 자격증명을 성공적으로 불러와 저장했습니다!")
            else:
                st.error("JSON 파일에 유효한 client_id 또는 client_secret이 없습니다.")
        except Exception as e:
            st.error(f"JSON 파일 파싱 실패: {str(e)}")

    client_id_input = st.text_input(
        "Google Client ID",
        value=saved_client_id,
        type="password",
        help="Google Cloud Console -> API 및 서비스 -> 사용자 인증 정보에서 발급받은 OAuth 클라이언트 ID",
    )
    client_secret_input = st.text_input(
        "Google Client Secret",
        value=saved_client_secret,
        type="password",
        help="Google Cloud Console에서 발급받은 OAuth 클라이언트 보안 비밀번호",
    )

    if st.button("자격증명 저장", use_container_width=True):
        db.set_setting("google_client_id", client_id_input.strip())
        db.set_setting("google_client_secret", client_secret_input.strip())
        st.success("자격증명이 데이터베이스에 안전하게 저장되었습니다.")
        st.rerun()

client_id = db.get_setting("google_client_id", "") or ""
client_secret = db.get_setting("google_client_secret", "") or ""

st.sidebar.divider()

# Quota Gauge
st.sidebar.subheader("📊 일일 API 쿼터 사용량")
today_quota = db.get_today_quota()
max_quota = 10000
quota_percent = min(1.0, today_quota / max_quota)

st.sidebar.progress(quota_percent)
col_q1, col_q2 = st.sidebar.columns(2)
col_q1.metric("오늘 소모 쿼터", f"{today_quota:,} U")
col_q2.metric("잔여 쿼터", f"{max(0, max_quota - today_quota):,} U")

if st.sidebar.button("🔄 쿼터/화면 새로고침", use_container_width=True):
    st.rerun()

recent_logs = db.get_recent_quota_logs(limit=5)
if recent_logs:
    with st.sidebar.expander("📋 최근 API 소모 내역 (5건)"):
        for log in recent_logs:
            st.caption(f"• `{log['created_at'][11:19]}`: +**{log['units']}U** ({log['action']})")

st.sidebar.caption(
    f"기본 무료 할당량(10,000 유닛)의 **{quota_percent * 100:.1f}%** 소모됨.\n"
    "TubeSSOT는 재생목록 캐시(`UU...`) 및 50개 배치 조회를 활용해 극단적인 쿼터 효율성을 보장합니다."
)

st.sidebar.divider()

# Connected channels in sidebar
tokens = db.get_all_tokens()
st.sidebar.markdown("### 🔗 연동된 YouTube 채널")
if tokens:
    st.sidebar.caption(f"총 **{len(tokens)}개** 채널 연동됨")
    for t in tokens:
        st.sidebar.success(f"📺 **{t['channel_title']}**\n\n`{t['channel_id']}`")
else:
    st.sidebar.warning("⚠️ 연동된 채널이 없습니다.\n\n'1. 연동 계정 관리' 탭에서 먼저 계정을 연동해 주세요.")

st.sidebar.divider()
st.sidebar.info("💡 **TubeSSOT v1.0**\n단일 진실 공급원(SSOT) 기반 YouTube 구독 자산 및 수명주기 관리자")


# --- OAuth Callback Handler (Redirect from Google) ---
if "error" in st.query_params:
    oauth_err = st.query_params.get("error")
    st.session_state["oauth_status"] = ("error", f"Google 인증이 취소되었거나 오류가 발생했습니다: {oauth_err}")
    st.query_params.clear()
    st.rerun()

if "code" in st.query_params and client_id and client_secret:
    auth_code = st.query_params["code"]
    print(f"[OAuth Callback] Received auth code, exchanging for tokens...", flush=True)
    with st.spinner("Google 계정 인증 정보를 등록하는 중입니다..."):
        try:
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8501/", "http://localhost:8501", "http://localhost:8080/"],
                }
            }
            # Attempt exchange with http://localhost:8501/
            flow = None
            for r_uri in ["http://localhost:8501/", "http://localhost:8501"]:
                try:
                    flow = Flow.from_client_config(
                        client_config,
                        scopes=collector.SCOPES,
                        redirect_uri=r_uri,
                    )
                    flow.autogenerate_code_verifier = False
                    flow.fetch_token(code=auth_code)
                    break
                except Exception as ex_token:
                    print(f"[OAuth Callback] Fetch token with {r_uri} failed: {ex_token}", flush=True)

            if not flow or not flow.credentials:
                raise RuntimeError("Google 서버로부터 토큰을 획득하지 못했습니다.")

            creds = flow.credentials
            yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
            my_channels = yt.channels().list(part="snippet", mine=True).execute()

            if my_channels.get("items"):
                ch_info = my_channels["items"][0]
                c_id = ch_info["id"]
                c_title = ch_info["snippet"]["title"]
            else:
                # Fallback: Google Account without dedicated YouTube channel
                c_id = f"user_{creds.client_id[:12]}"
                c_title = "Google Linked Account (기본 계정)"

            db.upsert_token(
                channel_id=c_id,
                email="Linked Google Account",
                title=c_title,
                refresh_token=creds.refresh_token,
                access_token=creds.token,
                expiry=creds.expiry,
            )
            print(f"[OAuth Callback] Successfully linked channel: {c_title} ({c_id})", flush=True)
            st.session_state["oauth_status"] = ("success", f"🎉 채널 **'{c_title}'** ({c_id}) 연동이 성공적으로 등록되었습니다!")
        except Exception as e:
            err_msg = f"계정 연동 실패: {str(e)}"
            print(f"[OAuth Callback Error] {err_msg}", flush=True)
            st.session_state["oauth_status"] = ("error", err_msg)
        finally:
            st.query_params.clear()
            st.rerun()


# Display persistent OAuth Status Message if present
if "oauth_status" in st.session_state:
    status_type, status_text = st.session_state.pop("oauth_status")
    if status_type == "success":
        st.success(status_text)
        st.balloons()
    else:
        st.error(status_text)

# Refresh tokens list
tokens = db.get_all_tokens()

# Main Top Status Banner
if tokens:
    connected_names = " | ".join([f"**{t['channel_title']}** (`{t['channel_id']}`)" for t in tokens])
    st.info(f"🔗 **현재 연동된 YouTube 채널 ({len(tokens)}개):** {connected_names}")
else:
    st.warning("⚠️ **연동된 YouTube 채널이 없습니다.** 아래 '1. 👥 연동 계정 관리' 탭에서 계정을 연동해 주세요.")


# --- MAIN CONTENT TABS ---
tab_accounts, tab_sync, tab_dashboard, tab_export = st.tabs([
    "1. 👥 연동 계정 관리",
    "2. 🔄 수집 및 동기화",
    "3. 📈 통합 구독 대시보드",
    "4. 📥 엑셀 내보내기",
])

# ==========================================
# TAB 1: 연동 계정 관리 (OAuth Token CRUD)
# ==========================================
with tab_accounts:
    st.subheader("연동된 YouTube 채널 목록")
    st.caption("개인 계정, 업무용 계정, 스터디용 브랜드 채널 등 여러 계정을 등록하여 통합 관리할 수 있습니다.")

    if tokens:
        for idx, t in enumerate(tokens):
            with st.container(border=True):
                col_info, col_id, col_btn = st.columns([4, 3, 1])
                with col_info:
                    st.markdown(f"### 📺 **{t['channel_title']}**")
                    st.caption(f"이메일: `{t['account_email']}` | 등록일: {t['created_at']}")
                with col_id:
                    st.code(t["channel_id"], language="text")
                with col_btn:
                    if st.button("연결 해제", key=f"del_{t['channel_id']}", type="secondary", use_container_width=True):
                        db.delete_token(t["channel_id"])
                        st.toast(f"'{t['channel_title']}' 채널 연동이 해제되었습니다.")
                        st.rerun()
    else:
        st.info("현재 등록된 YouTube 계정/채널이 없습니다. 아래에서 신규 채널을 연동해 주세요.")

    st.divider()
    st.subheader("신규 YouTube 채널 연동 (OAuth 2.0)")

    if not client_id or not client_secret:
        st.warning("⚠️ 사이드바에서 먼저 **Google Client ID**와 **Google Client Secret**을 입력하고 저장해 주세요.")
    else:
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost:8501/", "http://localhost:8501", "http://localhost:8080/"],
            }
        }

        try:
            flow = Flow.from_client_config(
                client_config,
                scopes=collector.SCOPES,
                redirect_uri="http://localhost:8501/",
            )
            flow.autogenerate_code_verifier = False
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

            st.markdown("""
            **원클릭 계정 연동 안내:**  
            아래 버튼을 클릭하면 Google 로그인 및 채널 선택 창이 열립니다.  
            구독 목록을 가져올 **계정 또는 브랜드 채널**을 선택하고 권한을 허용하시면, 자동으로 이 대시보드(`localhost:8501`)로 돌아오며 즉시 연동됩니다.
            """)

            st.link_button(
                "🚀 [원클릭] Google 계정 로그인 및 채널 선택하기",
                auth_url,
                type="primary",
                use_container_width=True,
            )
            st.caption("ℹ️ 클릭 시 Google 로그인 화면으로 이동합니다.")

        except Exception as e:
            st.error(f"인증 URL 생성 실패: {str(e)}")

        st.markdown("---")
        with st.expander("🛠️ 수동 인증 또는 다른 포트(8080) 리다이렉트 붙여넣기"):
            st.markdown("""
            Google 로그인 완료 후 브라우저 주소창이 `http://localhost:8080/?code=...` 등으로 이동하여 페이지가 열리지 않거나,
            직접 인증 코드를 복사하신 경우 아래에 붙여넣어 수동 등록하실 수 있습니다.
            """)
            col_m1, col_m2 = st.columns([3, 1])
            with col_m1:
                manual_input = st.text_input(
                    "주소창 전체 URL 또는 코드 붙여넣기:",
                    placeholder="http://localhost:8080/?code=4/0A... 또는 4/0A...",
                )
            with col_m2:
                redirect_choice = st.selectbox(
                    "사용한 리다이렉트 URI:",
                    ["http://localhost:8501/", "http://localhost:8501", "http://localhost:8080/"],
                )

            if st.button("수동 연동 등록 완료", use_container_width=True):
                if manual_input.strip():
                    try:
                        raw_val = manual_input.strip()
                        code = raw_val
                        if "code=" in raw_val:
                            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_val).query)
                            code = parsed_qs.get("code", [raw_val])[0]

                        manual_flow = Flow.from_client_config(
                            client_config,
                            scopes=collector.SCOPES,
                            redirect_uri=redirect_choice,
                        )
                        manual_flow.autogenerate_code_verifier = False
                        manual_flow.fetch_token(code=code)
                        creds = manual_flow.credentials
                        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
                        my_channels = yt.channels().list(part="snippet", mine=True).execute()

                        if my_channels.get("items"):
                            ch_info = my_channels["items"][0]
                            c_id = ch_info["id"]
                            c_title = ch_info["snippet"]["title"]
                        else:
                            c_id = f"user_{creds.client_id[:12]}"
                            c_title = "Google Linked Account (기본 계정)"

                        db.upsert_token(
                            channel_id=c_id,
                            email="Linked Google Account",
                            title=c_title,
                            refresh_token=creds.refresh_token,
                            access_token=creds.token,
                            expiry=creds.expiry,
                        )
                        st.session_state["oauth_status"] = ("success", f"🎉 채널 **'{c_title}'** ({c_id}) 수동 연동이 성공적으로 등록되었습니다!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"수동 연동 실패: {str(e)}")
                else:
                    st.warning("URL 또는 코드를 입력해 주세요.")


# ==========================================
# TAB 2: 데이터 수집 및 동기화 (Sync Runner)
# ==========================================
with tab_sync:
    st.subheader("데이터 동기화 오케스트레이터")
    st.write(
        "등록된 모든 연동 계정의 구독 목록을 순회 수집하여 `channel_id` 기준으로 중복을 병합하고,\n"
        "각 채널의 최신 영상 3개를 검사하여 활동성(Liveness: 🟢활성 / 🟡정체 / 🔴휴면 / ⚪미확인)을 판정합니다."
    )

    tokens = db.get_all_tokens()
    if not tokens:
        st.warning("⚠️ **등록된 연동 계정이 없습니다.** '1. 👥 연동 계정 관리' 탭으로 이동하여 YouTube 채널을 먼저 연동해 주세요.")
    elif not client_id or not client_secret:
        st.warning("⚠️ 사이드바에서 Client ID와 Secret을 먼저 설정하세요.")
    else:
        st.markdown("#### 📋 동기화 대상 연동 채널:")
        for t in tokens:
            st.markdown(f"- 📺 **{t['channel_title']}** (`{t['channel_id']}`) — *등록일: {t['created_at']}*")
        st.caption(f"총 **{len(tokens)}개** 채널의 구독 목록을 순회하여 중복 없이 수집합니다.")

        st.markdown("---")
        st.markdown("#### ⚙️ 동기화 범위 선택 (개발/운영 모드)")
        col_m1, col_m2 = st.columns([3, 2])
        with col_m1:
            range_option = st.selectbox(
                "분석 대상 채널 수 제한:",
                [
                    "전체 채널 수집 (All Channels - 운영 모드)",
                    "빠른 테스트 (10개만 - 초고속 개발용)",
                    "샘플 수집 (50개)",
                    "중간 수집 (100개)",
                    "직접 개수 입력",
                ],
                index=0,
                help="개발 중에는 10개 또는 50개만 부분 수집하여 시간과 API 쿼터를 대폭 절약할 수 있습니다.",
            )
        with col_m2:
            if range_option == "빠른 테스트 (10개만 - 초고속 개발용)":
                max_ch = 10
                st.info("⚡ 약 10~15 쿼터 소모 (약 1~2초 소요)")
            elif range_option == "샘플 수집 (50개)":
                max_ch = 50
                st.info("⚡ 약 55 쿼터 소모 (약 3~5초 소요)")
            elif range_option == "중간 수집 (100개)":
                max_ch = 100
                st.info("⚡ 약 105 쿼터 소모 (약 8~10초 소요)")
            elif range_option == "직접 개수 입력":
                max_ch = st.number_input("수집할 최대 채널 수:", min_value=1, max_value=5000, value=25)
            else:
                max_ch = None
                st.info("💡 전체 구독 목록 전수 수집 및 분석")

        btn_label = f"▶ 동기화 파이프라인 실행 ({max_ch}개 부분 수집)" if max_ch else "▶ 전체 동기화 파이프라인 실행"

        if st.button(btn_label, type="primary", use_container_width=True):
            progress_bar = st.progress(0.0)
            status_placeholder = st.empty()

            try:
                total_synced = collector.run_sync_pipeline(
                    client_id=client_id,
                    client_secret=client_secret,
                    progress_callback=lambda p, msg: (
                        progress_bar.progress(min(1.0, max(0.0, p))),
                        status_placeholder.info(f"⏳ {msg}"),
                    ),
                    max_channels=max_ch,
                )
                progress_bar.progress(1.0)
                status_placeholder.success(f"🎉 성공적으로 동기화가 완료되었습니다! (총 {total_synced}개 채널 수집 및 판정)")
                st.toast("동기화가 완료되었습니다. 사이드바와 대시보드가 업데이트됩니다.")
                st.rerun()
            except Exception as e:
                status_placeholder.error(f"❌ 동기화 중 오류가 발생했습니다: {str(e)}")


# ==========================================
# TAB 3: 통합 구독 관리 대시보드 (SSOT Dashboard)
# ==========================================
with tab_dashboard:
    records = db.fetch_master_records()

    if not records:
        st.info("ℹ️ 현재 저장된 구독 채널 데이터가 없습니다. '2. 수집 및 동기화' 탭에서 동기화를 먼저 실행해 주세요.")
    else:
        # Build pandas DataFrame
        data_list = []
        all_categories = set()
        all_sources = set()

        for r in records:
            cats = json.loads(r["categories"]) if r["categories"] else []
            sources = json.loads(r["source_accounts"]) if r["source_accounts"] else []
            for c in cats:
                all_categories.add(c)
            for s in sources:
                all_sources.add(s)

            data_list.append({
                "channel_id": r["channel_id"],
                "liveness_status": r["liveness_status"] or "UNKNOWN",
                "title": r["title"],
                "categories": cats,
                "categories_str": ", ".join(cats),
                "days_since_last_upload": r["days_since_last_upload"],
                "last_upload_at": r["last_upload_at"] or "-",
                "source_accounts": sources,
                "source_accounts_str": ", ".join(sources),
                "custom_url": r["custom_url"] or "",
                "description": r["description"] or "",
                "thumbnail_url": r["thumbnail_url"] or "",
                "recent_videos": json.loads(r["recent_videos"]) if r["recent_videos"] else [],
                "raw_subscription_json": json.loads(r["raw_subscription_json"]) if r["raw_subscription_json"] else {},
                "raw_channel_json": json.loads(r["raw_channel_json"]) if r["raw_channel_json"] else {},
            })

        df = pd.DataFrame(data_list)

        # Metrics KPI
        total_ch = len(df)
        count_green = len(df[df["liveness_status"] == "GREEN"])
        count_yellow = len(df[df["liveness_status"] == "YELLOW"])
        count_red = len(df[df["liveness_status"] == "RED"])
        count_unknown = len(df[df["liveness_status"] == "UNKNOWN"])

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("총 구독 채널", f"{total_ch:,} 개")
        m2.metric("🟢 활성 (≤90일)", f"{count_green:,} 개")
        m3.metric("🟡 정체 (91~180일)", f"{count_yellow:,} 개")
        m4.metric("🔴 휴면 (>180일)", f"{count_red:,} 개")
        m5.metric("⚪ 미확인 (영상 0개)", f"{count_unknown:,} 개")

        st.divider()

        # Multi-filters
        col_f1, col_f2, col_f3, col_f4 = st.columns([2, 3, 2, 2])
        status_opts = ["GREEN", "YELLOW", "RED", "UNKNOWN"]
        status_labels = {"GREEN": "🟢 활성", "YELLOW": "🟡 정체", "RED": "🔴 휴면", "UNKNOWN": "⚪ 미확인"}

        selected_statuses = col_f1.multiselect(
            "신호등 상태 필터",
            options=status_opts,
            default=status_opts,
            format_func=lambda x: status_labels.get(x, x),
        )
        search_keyword = col_f2.text_input("🔍 채널명 검색", placeholder="채널명을 입력하세요...")
        selected_category = col_f3.selectbox(
            "🏷️ 카테고리 필터",
            options=["전체"] + sorted(list(all_categories)),
        )
        selected_source = col_f4.selectbox(
            "👤 구독 소속 계정",
            options=["전체"] + sorted(list(all_sources)),
        )

        # Apply filtering
        filtered_df = df[df["liveness_status"].isin(selected_statuses)]
        if search_keyword.strip():
            filtered_df = filtered_df[filtered_df["title"].str.contains(search_keyword.strip(), case=False, na=False)]
        if selected_category != "전체":
            filtered_df = filtered_df[filtered_df["categories"].apply(lambda cats: selected_category in cats)]
        if selected_source != "전체":
            filtered_df = filtered_df[filtered_df["source_accounts"].apply(lambda srcs: selected_source in srcs)]

        st.caption(f"검색/필터 결과: **{len(filtered_df):,}** 개 채널")

        # Display Data Table
        display_cols = [
            "liveness_status",
            "title",
            "days_since_last_upload",
            "last_upload_at",
            "categories_str",
            "source_accounts_str",
            "custom_url",
        ]
        col_name_map = {
            "liveness_status": "상태",
            "title": "채널명",
            "days_since_last_upload": "미업로드 경과일수",
            "last_upload_at": "최근 업로드일",
            "categories_str": "카테고리",
            "source_accounts_str": "구독 소속 계정",
            "custom_url": "핸들/URL",
        }

        # Status badge mapping
        status_badge_map = {
            "GREEN": "🟢 활성",
            "YELLOW": "🟡 정체",
            "RED": "🔴 휴면",
            "UNKNOWN": "⚪ 미확인",
        }
        grid_df = filtered_df[display_cols].copy()
        grid_df["liveness_status"] = grid_df["liveness_status"].map(status_badge_map)
        grid_df.rename(columns=col_name_map, inplace=True)

        st.dataframe(grid_df, use_container_width=True, hide_index=True)

        # On-Demand Channel Inspector
        st.divider()
        st.subheader("🔍 온디맨드 채널 상세 검사 (On-Demand Inspector)")

        if not filtered_df.empty:
            channel_choices = filtered_df["title"].tolist()
            selected_ch_title = st.selectbox("상세 정보를 확인할 채널을 선택하세요:", channel_choices)

            if selected_ch_title:
                ch_row = filtered_df[filtered_df["title"] == selected_ch_title].iloc[0]

                header_col1, header_col2 = st.columns([1, 6])
                with header_col1:
                    if ch_row["thumbnail_url"]:
                        st.image(ch_row["thumbnail_url"], width=100)
                with header_col2:
                    st.markdown(f"### **{ch_row['title']}** {status_badge_map.get(ch_row['liveness_status'], '')}")
                    direct_url = f"https://www.youtube.com/{ch_row['custom_url']}" if ch_row["custom_url"] else f"https://www.youtube.com/channel/{ch_row['channel_id']}"
                    sub_url = f"{direct_url}?sub_confirmation=1"
                    st.markdown(f"[🌐 채널 바로가기]({direct_url}) | [🔔 원클릭 구독 링크]({sub_url})")
                    if ch_row["description"]:
                        st.caption(ch_row["description"][:300] + ("..." if len(ch_row["description"]) > 300 else ""))

                st.markdown("#### 🎬 최근 영상 3개")
                recent_vids = ch_row["recent_videos"]
                if recent_vids:
                    num_cols = max(1, min(3, len(recent_vids)))
                    v_cols = st.columns(num_cols)
                    for v_idx, v in enumerate(recent_vids[:num_cols]):
                        with v_cols[v_idx]:
                            with st.container(border=True):
                                if v.get("thumbnail_url"):
                                    st.image(v.get("thumbnail_url"), use_container_width=True)
                                st.markdown(f"**[{v.get('title')}]({v.get('video_url')})**")
                                st.caption(f"게시일: {v.get('published_at', '')[:10]}")
                else:
                    st.info("이 채널에 게시된 최신 영상이 없습니다.")

                with st.expander("🛠️ API 원본 JSON 페이로드 (Raw JSON) 검사"):
                    st.json({
                        "raw_subscription": ch_row["raw_subscription_json"],
                        "raw_channel": ch_row["raw_channel_json"],
                    })


# ==========================================
# TAB 4: 엑셀 내보내기 (Export Engine)
# ==========================================
with tab_export:
    st.subheader("📥 엑셀(.xlsx) 리포트 추출")
    st.write("통합 구독 목록을 상태별 조건부 서식과 상세 정보가 정돈된 엑셀 파일로 내려받아 영구 보존할 수 있습니다.")

    records = db.fetch_master_records()
    if not records:
        st.info("내보낼 구독 채널 데이터가 없습니다.")
    else:
        export_mode = st.radio("내보낼 데이터 범위 선택:", ["전체 마스터 데이터", "현재 필터링된 데이터"], horizontal=True)

        target_records = records
        if export_mode == "현재 필터링된 데이터" and "filtered_df" in locals() and not filtered_df.empty:
            filtered_ids = set(filtered_df["channel_id"].tolist())
            target_records = [r for r in records if r["channel_id"] in filtered_ids]

        st.write(f"추출 대상: **{len(target_records):,}** 개 채널")

        if st.button("📊 엑셀 파일 생성 및 준비", type="primary"):
            rows = []
            for r in target_records:
                vids = json.loads(r["recent_videos"]) if r["recent_videos"] else []
                sources = json.loads(r["source_accounts"]) if r["source_accounts"] else []
                cats = json.loads(r["categories"]) if r["categories"] else []
                c_url = f"https://www.youtube.com/{r['custom_url']}" if r["custom_url"] else f"https://www.youtube.com/channel/{r['channel_id']}"

                rows.append({
                    "상태": r["liveness_status"],
                    "채널명": r["title"],
                    "채널홈URL": c_url,
                    "카테고리": ", ".join(cats),
                    "최근 업로드일": r["last_upload_at"] or "-",
                    "미업로드 경과일수": r["days_since_last_upload"] if r["days_since_last_upload"] is not None else "-",
                    "구독계정출처": ", ".join(sources),
                    "최근영상1": vids[0]["title"] if len(vids) > 0 else "",
                    "최근영상2": vids[1]["title"] if len(vids) > 1 else "",
                    "최근영상3": vids[2]["title"] if len(vids) > 2 else "",
                    "채널설명": (r["description"] or "").replace("\n", " "),
                })

            export_df = pd.DataFrame(rows)

            # Build openpyxl workbook with styling
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                export_df.to_excel(writer, index=False, sheet_name="Subscriptions_SSOT")
                ws = writer.sheets["Subscriptions_SSOT"]

                # Header styles
                header_font = Font(name="Malgun Gothic", size=11, bold=True, color="FFFFFF")
                header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
                for cell in ws[1]:
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", vertical="center")

                # Conditional formatting for Status column (Column A)
                green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                green_font = Font(color="006100", bold=True)
                yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                yellow_font = Font(color="9C6500", bold=True)
                red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                red_font = Font(color="9C0006", bold=True)

                for row in range(2, len(rows) + 2):
                    status_cell = ws.cell(row=row, column=1)
                    val = str(status_cell.value)
                    if val == "GREEN":
                        status_cell.fill = green_fill
                        status_cell.font = green_font
                    elif val == "YELLOW":
                        status_cell.fill = yellow_fill
                        status_cell.font = yellow_font
                    elif val == "RED":
                        status_cell.fill = red_fill
                        status_cell.font = red_font
                    status_cell.alignment = Alignment(horizontal="center", vertical="center")

                # Auto-fit column widths
                for col in ws.columns:
                    col_letter = openpyxl.utils.get_column_letter(col[0].column)
                    max_len = max(len(str(cell.value or "")) for cell in col)
                    ws.column_dimensions[col_letter].width = min(50, max(max_len + 3, 12))

            excel_data = output.getvalue()
            filename = f"youtube_subscriptions_ssot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

            st.download_button(
                label="📥 엑셀(.xlsx) 파일 다운로드",
                data=excel_data,
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
