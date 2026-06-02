#!/usr/bin/env python3
"""
자동 업데이트 시스템
GitHub Releases를 통해 새 버전을 확인하고 다운로드
"""

import os
import sys
import json
import requests
import zipfile
import shutil
import subprocess
import time
from pathlib import Path
from packaging import version as pkg_version
def _load_version_py() -> dict:
    """파일시스템의 version.py를 직접 exec()하여 PYZ 고정 버전 문제를 우회."""
    _candidates = []
    if getattr(sys, "frozen", False):
        _exe_dir = Path(sys.executable).resolve().parent
        _candidates.append(_exe_dir / "_internal" / "version.py")
        _candidates.append(_exe_dir / "version.py")
    _candidates.append(Path(__file__).resolve().parent / "version.py")
    for _vp in _candidates:
        if _vp.is_file():
            _ns: dict = {}
            exec(compile(_vp.read_text(encoding="utf-8"), str(_vp), "exec"), _ns)
            return _ns
    return {}

_version_ns = _load_version_py()
__version__ = _version_ns.get("__version__", "0.0.0")
UPDATE_CHECK_URL = _version_ns.get("UPDATE_CHECK_URL", "")
__app_name__ = _version_ns.get("__app_name__", "KakaoManager")

# EXE variant: kakao_manager_v3 (기본)
def get_exe_variant() -> str:
    """실행 중인 EXE variant 판별."""
    if not getattr(sys, 'frozen', False):
        return 'kakao_manager_v3'
    try:
        exe_name = (Path(sys.executable).name or '').strip().lower()
        if 'kakaomanager' in exe_name.replace('-', ''):
            return 'kakao_manager_v3'
        return 'kakao_manager_v3'
    except Exception:
        return 'kakao_manager_v3'


# 안전한 출력 함수
def safe_print(msg):
    """인코딩 오류를 방지하는 안전한 print"""
    try:
        print(msg)
    except UnicodeEncodeError:
        # 이모지 제거하고 출력
        safe_msg = msg.encode('ascii', errors='ignore').decode('ascii')
        print(safe_msg)
    except Exception:
        pass

def _discover_gh_token() -> str | None:
    """gh CLI 또는 .km_github_token / .rt_github_token 파일에서 GitHub 토큰 탐색."""
    _exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path.cwd()

    for token_name in (".km_github_token", ".rt_github_token"):
        for base in [_exe_dir, Path.home()]:
            token_file = base / token_name
            if not token_file.is_file():
                continue
            try:
                t = token_file.read_text(encoding="utf-8").strip()
                if t:
                    return t
            except Exception:
                pass

    # gh auth token
    try:
        import subprocess as _sp
        r = _sp.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5,
                     creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        if r.returncode == 0 and r.stdout.strip():
            _token = r.stdout.strip()
            # 발견한 토큰을 EXE 디렉토리에 캐시 (다음 실행 시 빠르게 로드)
            try:
                (_exe_dir / ".km_github_token").write_text(_token, encoding="utf-8")
            except Exception:
                pass
            return _token
    except Exception:
        pass
    return None


class AutoUpdater:
    """자동 업데이트 매니저"""
    
    def __init__(self, current_version: str = __version__):
        self.current_version = current_version
        self.update_check_url = UPDATE_CHECK_URL
        
        # EXE 모드면 실행 파일 위치, 아니면 현재 디렉토리
        import sys
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path.cwd()
        
        # 앱 폴더 외부(상위 레벨)에 다운로드/스테이징 폴더 생성
        # → 업데이트 반복 시 경로가 깊어지지 않음
        self.download_dir = base_dir.parent / "_km_updates"

        try:
            self.download_dir.mkdir(exist_ok=True)
        except (PermissionError, OSError):
            # 상위 폴더 쓰기 불가 시 앱 내부 fallback
            try:
                self.download_dir = base_dir / "updates"
                self.download_dir.mkdir(exist_ok=True)
            except (PermissionError, OSError):
                import tempfile
                self.download_dir = Path(tempfile.gettempdir()) / "KakaoManager" / "updates"
                self.download_dir.mkdir(parents=True, exist_ok=True)
        
    def check_for_updates(self) -> dict:
        """
        새 버전 확인
        
        Returns:
            dict: {
                'update_available': bool,
                'latest_version': str,
                'download_url': str,
                'release_notes': str
            }
        """
        try:
            safe_print(f"🔍 업데이트 확인 중... (현재 버전: {self.current_version})")
            safe_print(f"   API URL: {self.update_check_url}")
            safe_print(f"   타임스탬프: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # GitHub API로 최신 릴리스 확인
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': f'{__app_name__}/{self.current_version}',
                'Cache-Control': 'no-cache',  # 캐시 방지
                'X-GitHub-Api-Version': '2022-11-28'
            }
            
            # GitHub 토큰 확인 (private repository 접근용)
            token = os.environ.get('GITHUB_TOKEN') or os.environ.get('RT_GITHUB_TOKEN')
            if not token:
                token = _discover_gh_token()
            if token:
                headers['Authorization'] = f'token {token}'
                safe_print(f"   🔑 GitHub 토큰 사용 중 (private repo 접근)")
            else:
                safe_print(f"   ⚠️ GitHub 토큰 없음 (private repo는 404 발생 가능)")
                safe_print(f"   💡 환경 변수 설정: set RT_GITHUB_TOKEN=your_token")

            # variant에 맞는 tag prefix로 릴리스 조회
            variant = get_exe_variant()
            try:
                VARIANT_TAG_PREFIX = _version_ns.get("VARIANT_TAG_PREFIX", {})
                RELEASES_LIST_URL = _version_ns.get("RELEASES_LIST_URL", None)
                tag_prefix = VARIANT_TAG_PREFIX.get(variant, "v") if VARIANT_TAG_PREFIX else "v"
            except Exception:
                tag_prefix = "v"
                RELEASES_LIST_URL = None

            _uses_independent_release = tag_prefix != "v"

            if _uses_independent_release and RELEASES_LIST_URL:
                safe_print(f"   독립 릴리스 모드: tag_prefix='{tag_prefix}' (variant={variant})")
                response = requests.get(
                    f"{RELEASES_LIST_URL}?per_page=30",
                    timeout=12,
                    headers=headers,
                )
            else:
                response = requests.get(
                    self.update_check_url,
                    timeout=8,
                    headers=headers,
                )
            
            safe_print(f"   응답 상태: {response.status_code}")
            safe_print(f"   응답 URL: {response.url}")
            
            if response.status_code == 404:
                if token:
                    safe_print(f"⚠️ private repository 접근 실패 (토큰 권한 확인 필요)")
                    safe_print(f"   필요 권한: repo (Full control of private repositories)")
                else:
                    safe_print(f"⚠️ private repository는 GitHub 토큰이 필요합니다")
                    safe_print(f"   해결 방법:")
                    safe_print(f"   1. GitHub → Settings → Developer settings → Personal access tokens")
                    safe_print(f"   2. 'Generate new token (classic)' 클릭")
                    safe_print(f"   3. 'repo' 권한 체크")
                    safe_print(f"   4. 토큰 생성 후 복사")
                    safe_print(f"   5. 환경 변수 설정: set GITHUB_TOKEN=토큰값")
                    safe_print(f"   6. 프로그램 재시작")
                
                return {
                    'update_available': False,
                    'latest_version': self.current_version,
                    'download_url': None,
                    'release_notes': '',
                    'is_latest': True,
                    'error': 'Private repository - GitHub 토큰 필요'
                }
            
            response.raise_for_status()

            if _uses_independent_release and RELEASES_LIST_URL:
                all_releases = response.json() or []
                matching = [r for r in all_releases
                            if r.get('tag_name', '').startswith(tag_prefix) and not r.get('draft')]
                if not matching:
                    safe_print(f"   ⚠️ tag_prefix='{tag_prefix}' 매칭 릴리스 없음 (전체 {len(all_releases)}개)")
                    return {
                        'update_available': False,
                        'latest_version': self.current_version,
                        'download_url': None,
                        'release_notes': '',
                        'is_latest': True,
                    }
                release_data = matching[0]
                latest_version_raw = release_data.get('tag_name', '')
                latest_version = latest_version_raw.removeprefix(tag_prefix).lstrip('v')
                safe_print(f"   매칭 릴리스: {latest_version_raw} → version={latest_version} (후보 {len(matching)}개)")
            else:
                release_data = response.json()
                latest_version_raw = release_data.get('tag_name', '')
                latest_version = latest_version_raw.lstrip('v')
                # 구버전 호환: VARIANT_TAG_PREFIX 없이 /releases/latest 사용 시
                # focus_upgraded-v3.84 같은 태그에서 버전만 추출
                if latest_version and not latest_version[0].isdigit():
                    for _vp in ["focus_upgraded-v", "whale_casefocus-v", "whale_login-v",
                                "focus_upgraded-", "whale_casefocus-", "whale_login-"]:
                        if latest_version_raw.startswith(_vp):
                            latest_version = latest_version_raw[len(_vp):]
                            break
            
            safe_print(f"")
            safe_print(f"   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            safe_print(f"   📦 Release 정보:")
            safe_print(f"      Tag (원본): {latest_version_raw}")
            safe_print(f"      Version (파싱): {latest_version}")
            safe_print(f"      현재 버전: {self.current_version}")
            safe_print(f"   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            safe_print(f"")
            
            # 버전 비교 (더 강력한 로직)
            def normalize_version(ver_str: str) -> str:
                """버전 문자열을 정규화 (1.7 → 1.70.0, 1.33 → 1.33.0)"""
                parts = ver_str.split('.')
                # 최소 3개 부분으로 확장 (major.minor.patch)
                while len(parts) < 3:
                    parts.append('0')
                # minor 버전을 두 자리로 확장 (1.7 → 1.70)
                if len(parts) >= 2:
                    minor = parts[1]
                    # 한 자리 minor 버전을 두 자리로 확장
                    if len(minor) == 1:
                        parts[1] = minor + '0'
                return '.'.join(parts)
            
            try:
                # 버전 정규화
                current_normalized = normalize_version(self.current_version)
                latest_normalized = normalize_version(latest_version)
                
                current_parsed = pkg_version.parse(current_normalized)
                latest_parsed = pkg_version.parse(latest_normalized)
                
                update_available = latest_parsed > current_parsed
                
                safe_print(f"   🔍 버전 비교 (packaging.version):")
                safe_print(f"      현재 (원본): {self.current_version} → (정규화): {current_normalized}")
                safe_print(f"      최신 (원본): {latest_version} → (정규화): {latest_normalized}")
                safe_print(f"      현재 (파싱됨): {current_parsed}")
                safe_print(f"      최신 (파싱됨): {latest_parsed}")
                safe_print(f"      비교 결과: {latest_parsed} > {current_parsed} = {update_available}")
                safe_print(f"")
                
            except Exception as version_error:
                safe_print(f"⚠️ 버전 파싱 오류: {version_error}")
                # 문자열 비교로 폴백
                update_available = latest_version != self.current_version
                safe_print(f"   폴백 비교: {self.current_version} != {latest_version} = {update_available}")
            
            # 다운로드 URL 찾기: 현재 EXE variant에 맞는 zip만 선택
            safe_print(f"   EXE variant: {variant}")
            download_url = None
            assets = release_data.get('assets', []) or []
            asset_count = len(assets)
            safe_print(f"   업로드된 파일 수: {asset_count}")

            def _asset_dl_url(asset):
                """프라이빗 레포: API URL 우선, 공개 레포: browser_download_url 사용."""
                api_url = asset.get('url', '')
                browser_url = asset.get('browser_download_url', '')
                if token and api_url:
                    return api_url
                return browser_url

            def pick_download_url(assets_list):
                # variant별 선호 zip 패턴
                if variant == 'whale_casefocus':
                    preferred_zip_names = [
                        f"{__app_name__}_whale_casefocus_v{latest_version}.zip",
                        f"RoboTraffic_whale_casefocus_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"{__app_name__}_whale_casefocus_v{latest_version}.exe"
                elif variant == 'whale_login':
                    preferred_zip_names = [
                        f"{__app_name__}_whale_login_v{latest_version}.zip",
                        f"RoboTraffic_whale_login_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"{__app_name__}_whale_login_v{latest_version}.exe"
                elif variant == 'focus_upgraded':
                    preferred_zip_names = [
                        f"{__app_name__}_focus_upgraded_v{latest_version}.zip",
                        f"RoboTraffic_focus_upgraded_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"{__app_name__}_focus_upgraded_v{latest_version}.exe"
                elif variant == 'manual_safe':
                    preferred_zip_names = [
                        f"{__app_name__}_manual_safe_v{latest_version}.zip",
                        f"RoboTraffic_manual_safe_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"{__app_name__}_manual_safe_v{latest_version}.exe"
                elif variant == 'case':
                    preferred_zip_names = [
                        f"{__app_name__}_case_v{latest_version}.zip",
                        f"RoboTraffic_case_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"{__app_name__}_case_v{latest_version}.exe"
                elif variant == 'kakao_manager_v3':
                    preferred_zip_names = [
                        f"KakaoManager-V3_v{latest_version}.zip",
                        f"{__app_name__}-V3_v{latest_version}.zip",
                        f"{__app_name__}_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"KakaoManager-V3.exe"
                else:
                    preferred_zip_names = [
                        f"KakaoManager-V3_v{latest_version}.zip",
                        f"{__app_name__}_v{latest_version}.zip",
                    ]
                    preferred_exe_name = f"KakaoManager-V3.exe"
                exe_assets = []
                zip_assets = []
                for asset in assets_list:
                    name = asset.get('name', '')
                    lower = name.lower()
                    safe_print(f"   - {name} ({asset.get('size', 0) / 1024 / 1024:.1f}MB)")
                    if name in preferred_zip_names:
                        return _asset_dl_url(asset)
                    if name == preferred_exe_name:
                        return _asset_dl_url(asset)
                    if lower.endswith('.exe'):
                        exe_assets.append(asset)
                    elif lower.endswith('.zip'):
                        zip_assets.append(asset)
                _variant_keywords = {
                    'whale_casefocus': 'whale_casefocus',
                    'whale_login': 'whale_login',
                    'focus_upgraded': 'focus_upgraded',
                    'manual_safe': 'manual_safe',
                    'case': '_case_',
                    'kakao_manager_v3': 'KakaoManager-V3',
                }
                for asset in zip_assets:
                    aname = asset.get('name', '')
                    if variant in _variant_keywords:
                        if _variant_keywords[variant] in aname and aname.endswith('.zip'):
                            return _asset_dl_url(asset)
                    elif variant == 'gui':
                        if '_case_' not in aname and 'manual_safe' not in aname and 'focus_upgraded' not in aname and aname.endswith('.zip'):
                            return _asset_dl_url(asset)
                if zip_assets:
                    zip_assets.sort(key=lambda a: a.get('size', 0), reverse=True)
                    return _asset_dl_url(zip_assets[0])
                if exe_assets:
                    exe_assets.sort(key=lambda a: a.get('size', 0), reverse=True)
                    return exe_assets[0].get('browser_download_url')
                return None

            download_url = pick_download_url(assets)

            # 새 버전인데 URL이 없으면: 빈 자산이든 variant 일시 불일치든 GitHub 전파 지연일 수 있음
            if update_available and not download_url:
                _why = "자산 목록 비어 있음" if asset_count == 0 else "zip URL 미매칭(전파 지연 가능)"
                safe_print(f"   ⏳ {_why}. 최대 5회 재조회...")
                _retry_url = (f"{RELEASES_LIST_URL}?per_page=30"
                              if (_uses_independent_release and RELEASES_LIST_URL)
                              else self.update_check_url)
                for _ri in range(5):
                    time.sleep(4 if asset_count == 0 else 6)
                    r2 = requests.get(_retry_url, timeout=25, headers=headers)
                    if r2.status_code == 200:
                        if _uses_independent_release and RELEASES_LIST_URL:
                            _all2 = r2.json() or []
                            _match2 = [r for r in _all2 if r.get('tag_name', '').startswith(tag_prefix) and not r.get('draft')]
                            rd2 = _match2[0] if _match2 else {}
                        else:
                            rd2 = r2.json() or {}
                        assets2 = rd2.get("assets", []) or []
                        release_data = rd2
                        asset_count = len(assets2)
                        download_url = pick_download_url(assets2)
                        if download_url:
                            safe_print(f"   🔁 재조회 성공 ({_ri + 1}회차): 자산 {asset_count}개")
                            break
                        safe_print(f"   🔁 재조회 {_ri + 1}/5: 자산 {asset_count}개, 아직 URL 없음")
                    else:
                        safe_print(f"   재조회 응답 코드: {r2.status_code}")
            
            if not download_url and update_available:
                safe_print(f"⚠️ 다운로드 파일을 찾을 수 없습니다 (assets: {asset_count})")
            
            result = {
                'update_available': update_available,
                'latest_version': latest_version,
                'download_url': download_url,
                'release_notes': release_data.get('body', ''),
                'release_name': release_data.get('name', ''),
                'published_at': release_data.get('published_at', ''),
                'asset_count': asset_count
            }
            
            if update_available:
                safe_print(f"✅ 새 버전 발견: v{latest_version} (현재: v{self.current_version})")
            else:
                safe_print(f"✅ 최신 버전 사용 중 (v{self.current_version})")
            
            return result
            
        except requests.exceptions.ConnectionError as e:
            safe_print(f"❌ 네트워크 연결 오류: 인터넷 연결을 확인하세요")
            safe_print(f"   상세: {e}")
            return {
                'update_available': False,
                'latest_version': self.current_version,
                'download_url': None,
                'release_notes': '',
                'error': f'네트워크 연결 오류: {e}'
            }
            
        except requests.exceptions.Timeout as e:
            safe_print(f"❌ 요청 시간 초과: GitHub 서버 응답이 없습니다")
            safe_print(f"   상세: {e}")
            return {
                'update_available': False,
                'latest_version': self.current_version,
                'download_url': None,
                'release_notes': '',
                'error': f'시간 초과: {e}'
            }
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'N/A'
            safe_print(f"❌ HTTP 오류 (코드: {status_code}): {e}")
            
            if status_code == 403:
                safe_print(f"   GitHub API 요청 제한에 도달했을 수 있습니다")
            elif status_code == 404:
                safe_print(f"   릴리즈가 아직 생성되지 않았습니다")
            
            return {
                'update_available': False,
                'latest_version': self.current_version,
                'download_url': None,
                'release_notes': '',
                'error': f'HTTP {status_code}: {e}'
            }
            
        except requests.RequestException as e:
            safe_print(f"❌ 요청 오류: {e}")
            return {
                'update_available': False,
                'latest_version': self.current_version,
                'download_url': None,
                'release_notes': '',
                'error': str(e)
            }
            
        except Exception as e:
            safe_print(f"❌ 예기치 않은 오류: {e}")
            import traceback
            safe_print(traceback.format_exc())
            return {
                'update_available': False,
                'latest_version': self.current_version,
                'download_url': None,
                'release_notes': '',
                'error': str(e)
            }
    
    def download_update(self, download_url: str, filename: str = None,
                        progress_callback=None) -> Path:
        """
        업데이트 파일 다운로드

        Args:
            download_url: 다운로드 URL
            filename: 저장할 파일명 (None이면 자동)
            progress_callback: fn(downloaded_bytes, total_bytes, percent, speed_mbps)

        Returns:
            Path: 다운로드된 파일 경로
        """
        def _cr_total(cr: str) -> int:
            if not cr or "/" not in cr:
                return 0
            tail = cr.strip().split("/")[-1]
            return int(tail) if tail.isdigit() else 0

        if not filename:
            filename = download_url.split('/')[-1]
        if filename.isdigit():
            filename = f"update_{filename}.zip"

        filepath = self.download_dir / filename

        _connect_t = int(os.environ.get("ROBOTRAFFIC_UPDATE_CONNECT_TIMEOUT", "60"))
        _read_t = int(os.environ.get("ROBOTRAFFIC_UPDATE_READ_TIMEOUT", "3600"))
        _timeout = (_connect_t, _read_t)
        _max_retries = max(1, int(os.environ.get("ROBOTRAFFIC_UPDATE_DOWNLOAD_RETRIES", "10")))
        _chunk = int(os.environ.get("ROBOTRAFFIC_UPDATE_CHUNK_BYTES", str(256 * 1024)))
        _resume = os.environ.get("ROBOTRAFFIC_UPDATE_RESUME", "1").strip().lower() in ("1", "true", "yes")

        dl_headers = {'User-Agent': f'{__app_name__}/{self.current_version}'}
        _token = os.environ.get('GITHUB_TOKEN') or os.environ.get('RT_GITHUB_TOKEN') or _discover_gh_token()
        is_api_url = 'api.github.com' in download_url
        if is_api_url:
            dl_headers['Accept'] = 'application/octet-stream'
            if _token:
                dl_headers['Authorization'] = f'token {_token}'
        elif _token:
            dl_headers['Authorization'] = f'token {_token}'

        _retryable = (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        )

        last_err: Exception | None = None
        for attempt in range(_max_retries):
            try:
                if attempt > 0:
                    _wait = min(120, 5 * (2 ** min(attempt - 1, 4)))
                    safe_print(f"   ⏳ {_wait}초 후 다운로드 재시도 ({attempt + 1}/{_max_retries}, 부분파일 이어받기 가능)...")
                    time.sleep(_wait)

                safe_print(f"📥 업데이트 다운로드 중: {filename} (시도 {attempt + 1}/{_max_retries})")
                safe_print(f"   URL: {download_url[:80]}...")
                if is_api_url:
                    safe_print(f"   GitHub API URL (private repo 가능)")
                safe_print(f"   타임아웃: connect={_connect_t}s read={_read_t}s, 청크={_chunk // 1024}KB, resume={_resume}")

                if not _resume and filepath.exists():
                    try:
                        filepath.unlink()
                    except Exception:
                        pass

                _partial_for_range = filepath.stat().st_size if filepath.exists() else 0
                hdrs = dict(dl_headers)
                if _resume and _partial_for_range >= 1024 * 1024:
                    hdrs["Range"] = f"bytes={_partial_for_range}-"
                    safe_print(f"   ↪ 이어받기: {_partial_for_range / (1024*1024):.1f} MB 지점부터")

                response = requests.get(
                    download_url, stream=True, timeout=_timeout,
                    headers=hdrs, allow_redirects=True,
                )

                if response.status_code == 416:
                    safe_print("   ⚠ Range 416 → 부분파일 삭제 후 처음부터")
                    try:
                        filepath.unlink(missing_ok=True)
                    except Exception:
                        pass
                    hdrs.pop("Range", None)
                    response = requests.get(
                        download_url, stream=True, timeout=_timeout,
                        headers=hdrs, allow_redirects=True,
                    )

                if response.status_code not in (200, 206):
                    response.raise_for_status()

                cd = response.headers.get('content-disposition', '')
                if _partial_for_range == 0 and 'filename=' in cd:
                    import re as _re
                    _m = _re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
                    if _m:
                        real_name = _m.group(1).strip()
                        if real_name:
                            filepath = self.download_dir / real_name
                            filename = real_name

                partial = filepath.stat().st_size if filepath.exists() else 0

                if response.status_code == 206:
                    cr = response.headers.get("Content-Range", "")
                    total_size = _cr_total(cr)
                    cl = int(response.headers.get("Content-Length", 0) or 0)
                    if total_size <= 0:
                        total_size = partial + cl if cl > 0 else 0
                    downloaded = partial
                    fmode = "ab"
                else:
                    if partial > 0 and "Range" in hdrs:
                        safe_print("   ⚠ 서버가 전체 200 응답 → 부분파일 폐기 후 재수신")
                        try:
                            filepath.unlink(missing_ok=True)
                        except Exception:
                            pass
                        partial = 0
                    total_size = int(response.headers.get("content-length", 0) or 0)
                    downloaded = 0
                    fmode = "wb"

                _start_t = time.time()
                _last_log_t = 0.0

                safe_print(f"   전체 크기(추정): {total_size / (1024*1024):.1f} MB" if total_size else "   전체 크기: 알 수 없음")

                with open(filepath, fmode) as f:
                    for chunk in response.iter_content(chunk_size=_chunk):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            elapsed = now - _start_t
                            speed_mbps = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            percent = (downloaded / total_size * 100) if total_size > 0 else 0

                            if progress_callback:
                                try:
                                    progress_callback(downloaded, total_size, percent, speed_mbps)
                                except Exception:
                                    pass

                            if now - _last_log_t >= 5.0:
                                safe_print(
                                    f"  {percent:.0f}% ({downloaded/(1024*1024):.1f}/"
                                    f"{max(total_size/(1024*1024), 0.001):.1f} MB) - {speed_mbps:.1f} MB/s"
                                    if total_size
                                    else f"  {downloaded/(1024*1024):.1f} MB - {speed_mbps:.1f} MB/s"
                                )
                                _last_log_t = now

                _final_sz = filepath.stat().st_size
                if total_size > 0 and _final_sz != total_size:
                    raise IOError(f"불완전 다운로드: {_final_sz} != {total_size}")

                if progress_callback and total_size > 0:
                    try:
                        progress_callback(total_size, total_size, 100.0, 0)
                    except Exception:
                        pass
                safe_print(f"✅ 다운로드 완료: {filepath} ({_final_sz/(1024*1024):.1f} MB)")
                return filepath

            except _retryable as e:
                last_err = e
                safe_print(f"❌ 다운로드 네트워크 오류 (시도 {attempt + 1}/{_max_retries}): {e}")
                if attempt + 1 >= _max_retries:
                    break
            except Exception as e:
                safe_print(f"❌ 다운로드 실패: {e}")
                raise

        if last_err:
            raise last_err
        raise RuntimeError("download_update: 재시도 후에도 실패")
    
    def apply_update(self, update_file: Path, extract_progress_callback=None, restart_instance_count: int = 1) -> bool:
        """
        업데이트 적용

        Args:
            update_file: 업데이트 파일 경로
            extract_progress_callback: fn(current_idx, total_files, filename) - 압축 해제 진행률
            restart_instance_count: 업데이트 후 재시작할 EXE 인스턴스 수 (다중 실행 지원)

        Returns:
            bool: 성공 여부
        """
        try:
            safe_print(f"🔧 업데이트 적용 중...")

            if getattr(sys, 'frozen', False):
                current_exe = Path(sys.executable)
                app_dir = current_exe.parent
            else:
                app_dir = Path(__file__).parent
                current_exe = None

            if update_file.suffix.lower() == '.zip':
                if current_exe is not None:
                    # ── 1) Python에서 압축 해제 (진행률 표시 가능) ──
                    staging = self.download_dir / "staging_extract"
                    if staging.exists():
                        safe_print("  이전 스테이징 폴더 정리 중...")
                        try:
                            shutil.rmtree(staging)
                        except Exception:
                            shutil.rmtree(staging, ignore_errors=True)
                            if staging.exists():
                                import random, string
                                _old = staging.parent / f"staging_old_{''.join(random.choices(string.digits, k=6))}"
                                try:
                                    staging.rename(_old)
                                    safe_print(f"  잠긴 스테이징 → {_old.name} 으로 이동")
                                except Exception as _mv_err:
                                    safe_print(f"  ⚠️ 스테이징 정리 실패(무시): {_mv_err}")
                    staging.mkdir(parents=True, exist_ok=True)

                    safe_print("  압축 해제 시작...")
                    with zipfile.ZipFile(update_file, 'r') as zf:
                        members = zf.namelist()
                        total = len(members)
                        for idx, member in enumerate(members, 1):
                            zf.extract(member, staging)
                            if extract_progress_callback and idx % 50 == 0:
                                try:
                                    extract_progress_callback(idx, total, member.split('/')[-1])
                                except Exception:
                                    pass
                        if extract_progress_callback:
                            try:
                                extract_progress_callback(total, total, "완료")
                            except Exception:
                                pass
                    safe_print(f"  압축 해제 완료: {total} 파일")

                    # staging 내부 구조 확인
                    src_dir = staging
                    candidate = staging / app_dir.name
                    if candidate.exists() and candidate.is_dir():
                        src_dir = candidate

                    # ── 2) 배치 스크립트: kill → robocopy → 재시작 (빠름, ~5초) ──
                    batch_script = self.download_dir / "update_apply_folder.bat"
                    _log_file = self.download_dir / "update_apply.log"
                    exe_path = app_dir / current_exe.name
                    _n_instances = max(1, min(restart_instance_count, 10))

                    # N개 인스턴스 실행 명령 생성
                    _launch_lines = []
                    for _i in range(_n_instances):
                        _launch_lines.append(
                            f'echo [%date% %time%] launching exe instance {_i + 1}/{_n_instances} >> "%LOGFILE%"\n'
                            f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"Start-Process '{exe_path}'\" >> \"%LOGFILE%\" 2>&1\n"
                            f'echo [%date% %time%] launch rc=%errorlevel% >> "%LOGFILE%"'
                        )
                        if _i < _n_instances - 1:
                            _launch_lines.append("timeout /t 4 /nobreak >nul")
                    _launch_block = "\n".join(_launch_lines)

                    with open(batch_script, 'w', encoding='ascii', errors='replace') as f:
                        f.write(f"""@echo off
set "LOGFILE={_log_file}"
echo [%date% %time%] === update batch start (instances={_n_instances}) === >> "%LOGFILE%"

echo [%date% %time%] taskkill >> "%LOGFILE%"
taskkill /F /IM "{current_exe.name}" >nul 2>&1
echo [%date% %time%] taskkill rc=%errorlevel% >> "%LOGFILE%"

echo [%date% %time%] waiting for process exit... >> "%LOGFILE%"
set RETRIES=0
:wait_loop
tasklist /FI "IMAGENAME eq {current_exe.name}" 2>nul | find /I "{current_exe.name}" >nul
if %errorlevel%==0 (
    set /a RETRIES+=1
    if %RETRIES% GEQ 15 (
        echo [%date% %time%] WARNING: process still alive after 15s, proceeding anyway >> "%LOGFILE%"
        goto do_copy
    )
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo [%date% %time%] process exited after %RETRIES%s >> "%LOGFILE%"

:do_copy
echo [%date% %time%] robocopy >> "%LOGFILE%"
robocopy "{src_dir}" "{app_dir}" /E /R:5 /W:2 /NFL /NDL /NJH /NJS /NP /XD _km_updates _rt_updates __pycache__ >> "%LOGFILE%" 2>&1
set ROBO_RC=%errorlevel%
echo [%date% %time%] robocopy rc=%ROBO_RC% >> "%LOGFILE%"

if %ROBO_RC% GEQ 8 (
    echo [%date% %time%] ERROR: robocopy failed rc=%ROBO_RC%, retrying after 5s... >> "%LOGFILE%"
    timeout /t 5 /nobreak >nul
    robocopy "{src_dir}" "{app_dir}" /E /R:10 /W:3 /NFL /NDL /NJH /NJS /NP /XD _km_updates _rt_updates __pycache__ >> "%LOGFILE%" 2>&1
    echo [%date% %time%] robocopy retry rc=%errorlevel% >> "%LOGFILE%"
)

echo [%date% %time%] cleanup staging >> "%LOGFILE%"
if exist "{staging}" rmdir /S /Q "{staging}" >nul 2>&1
del /Q "{update_file}" >nul 2>&1

echo [%date% %time%] clearing pyc cache >> "%LOGFILE%"
del /S /Q "{app_dir}\\*.pyc" >nul 2>&1
for /d /r "{app_dir}" %%d in (__pycache__) do if exist "%%d" rmdir /S /Q "%%d" >nul 2>&1
echo [%date% %time%] pyc cache cleared >> "%LOGFILE%"

del /Q "{app_dir}\\_update_restart_state_*" >nul 2>&1
del /Q "{app_dir}\\_update_active.lock" >nul 2>&1
del /Q "{app_dir}\\_update_booster.lock" >nul 2>&1
del /Q "{app_dir}\\_update_expected_instances.txt" >nul 2>&1

{_launch_block}
echo [%date% %time%] === batch done === >> "%LOGFILE%"
del "%~f0"
""")
                    safe_print(f"✅ 파일 복사 및 재시작 배치 실행 중... (~5초)")
                    safe_print(f"   로그: {_log_file}")
                    subprocess.Popen(
                        ["cmd.exe", "/c", str(batch_script)],
                        creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0),
                    )
                    os._exit(0)

                # 개발/스크립트 모드: in-process 교체(잠금 리스크가 낮음)
                temp_dir = self.download_dir / "temp_extract"
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
                temp_dir.mkdir(exist_ok=True)

                safe_print("  압축 해제 중...")
                with zipfile.ZipFile(update_file, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # zip 내부가 "<앱폴더>/" 구조면 그 아래를 app_dir로 덮어씌움(삭제 없이 overwrite)
                src_dir = temp_dir
                try:
                    candidate = temp_dir / app_dir.name
                    if candidate.exists() and candidate.is_dir():
                        src_dir = candidate
                except Exception:
                    pass

                safe_print("  파일 복사 중...")
                for item in src_dir.iterdir():
                    dest = app_dir / item.name
                    if item.is_dir():
                        # 기존 폴더가 있으면 내용만 덮어쓰기(삭제하지 않음)
                        dest.mkdir(parents=True, exist_ok=True)
                        for sub in item.rglob("*"):
                            rel = sub.relative_to(item)
                            out = dest / rel
                            if sub.is_dir():
                                out.mkdir(parents=True, exist_ok=True)
                            else:
                                out.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(sub, out)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dest)

                shutil.rmtree(temp_dir)
            
            # EXE 파일인 경우 (단일 실행 파일)
            elif update_file.suffix.lower() == '.exe':
                if current_exe:
                    # 업데이트 스크립트 생성 (배치 파일)
                    batch_script = self.download_dir / "update_apply.bat"
                    with open(batch_script, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(f"""@echo off
echo 업데이트 적용 중...
timeout /t 2 /nobreak >nul
taskkill /F /IM "{current_exe.name}" >nul 2>&1
move /Y "{update_file}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
                    
                    safe_print(f"✅ 업데이트 준비 완료. 재시작합니다...")
                    
                    # 배치 스크립트 실행 후 현재 프로세스 종료
                    subprocess.Popen([str(batch_script)], shell=True)
                    sys.exit(0)
                else:
                    safe_print(f"⚠️ Python 스크립트 모드에서는 EXE 업데이트 불가")
                    return False
            
            safe_print(f"✅ 업데이트 적용 완료")
            return True
            
        except Exception as e:
            safe_print(f"❌ 업데이트 적용 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def check_and_update(self, auto_apply: bool = False) -> bool:
        """
        업데이트 확인 및 적용
        
        Args:
            auto_apply: 자동으로 업데이트 적용 여부
            
        Returns:
            bool: 업데이트 적용 여부
        """
        update_info = self.check_for_updates()
        
        if not update_info['update_available']:
            return False
        
        safe_print(f"\n{'='*80}")
        safe_print(f"🎉 새 버전 발견!")
        safe_print(f"{'='*80}")
        safe_print(f"현재 버전: {self.current_version}")
        safe_print(f"최신 버전: {update_info['latest_version']}")
        safe_print(f"\n📝 릴리스 노트:")
        safe_print(update_info['release_notes'][:500])
        safe_print(f"{'='*80}\n")
        
        if not auto_apply:
            response = input("업데이트를 다운로드하고 적용하시겠습니까? (y/n): ").strip().lower()
            if response != 'y':
                safe_print("업데이트를 건너뜁니다.")
                return False
        
        if not update_info['download_url']:
            safe_print("❌ 다운로드 URL을 찾을 수 없습니다.")
            return False
        
        try:
            # 다운로드
            update_file = self.download_update(update_info['download_url'])
            
            # 적용
            success = self.apply_update(update_file)
            return success
            
        except Exception as e:
            safe_print(f"❌ 업데이트 실패: {e}")
            return False


def main():
    """테스트용 메인 함수"""
    safe_print(f"🤖 {__app_name__} 업데이트 확인")
    safe_print(f"버전: {__version__}\n")
    
    updater = AutoUpdater()
    updater.check_and_update(auto_apply=False)


if __name__ == "__main__":
    main()

