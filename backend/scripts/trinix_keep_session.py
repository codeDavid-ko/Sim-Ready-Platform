#!/usr/bin/env python3
"""trinix_keep_session.py — Trinix 에디터 세션 키퍼 + export 다운로드 캡처.

퍼시스턴트 프로필로 Trinix 에디터를 열어 로그인/페어링을 유지하고, 그 탭(및 팝업)에서
발생하는 **모든 다운로드를 drop 디렉터리에 저장**한다. Trinix MCP 의 export_scene 이
페어링된 이 브라우저에서 파일 export(브라우저 다운로드)를 일으키면 여기서 잡아
백엔드(trinix_model)가 그 파일을 회수한다.

사용:
  python trinix_keep_session.py --profile <dir> --editor "<URL>" --drop <dir> --first-login
  # 최초: --first-login(headful)로 띄워 직접 로그인 → 그대로 두면 상주+다운로드 캡처
  # 이후: --first-login 없이 실행 → 저장 쿠키로 자동 로그인 + 상주
의존: pip install playwright && playwright install chromium
"""
import argparse
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="퍼시스턴트 프로필 디렉터리(쿠키 저장)")
    ap.add_argument("--editor", required=True, help="Trinix 에디터 URL(프로젝트 지정)")
    ap.add_argument("--drop", required=True, help="export 다운로드를 저장할 디렉터리")
    ap.add_argument("--first-login", action="store_true", help="최초 수동 로그인 모드(headful)")
    ap.add_argument("--headless", action="store_true", help="강제 헤드리스")
    a = ap.parse_args()

    drop = Path(a.drop)
    drop.mkdir(parents=True, exist_ok=True)

    def on_download(dl):
        try:
            name = dl.suggested_filename or "export.bin"
            dest = drop / name
            dl.save_as(str(dest))
            # 최신 파일 표시용 마커(백엔드가 빠르게 찾도록)
            (drop / "_latest.txt").write_text(str(dest), encoding="utf-8")
            print(f"[keeper] download captured -> {dest}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[keeper] download error: {e}", flush=True)

    def attach(pg):
        try:
            pg.on("download", on_download)
        except Exception:  # noqa: BLE001
            pass

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            a.profile,
            headless=(a.headless and not a.first_login),
            accept_downloads=True,
        )
        ctx.on("page", attach)
        for pg in ctx.pages:
            attach(pg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(a.editor, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:  # noqa: BLE001 -- Basic auth/로그인 게이트면 실패할 수 있음 → 창은 유지
            print(f"[keeper] 초기 이동 경고(로그인 필요할 수 있음): {e}", flush=True)
        print(f"[keeper] opened editor; downloads -> {drop}", flush=True)
        if a.first_login:
            print("[keeper] 보이는 브라우저 창에서 직접 로그인하세요(주소창에 에디터 URL 입력/기본 인증 포함). "
                  "로그인 후 창은 그대로 두면 됩니다(상주).", flush=True)

        # 무기한 상주 — 세션/페어링 유지 + 다운로드 캡처.
        # first-login 중에는 사용자가 직접 운전하므로 자동 재이동하지 않는다.
        while True:
            time.sleep(15)
            if a.first_login:
                continue
            try:
                if "/editor" not in (page.url or ""):
                    page.goto(a.editor, wait_until="domcontentloaded", timeout=20000)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
