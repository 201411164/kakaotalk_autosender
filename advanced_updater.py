#!/usr/bin/env python3
"""
고급 업데이트 시스템
- 무결성 검증
- 롤백 지원
- 백업 관리
"""

import hashlib
import hmac
import shutil
import json
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Optional
import sys
import subprocess

from updater import AutoUpdater, safe_print, _version_ns

__version__ = _version_ns.get("__version__", "0.0.0")


class AdvancedUpdater(AutoUpdater):
    """고급 기능이 추가된 업데이트 시스템"""

    def _should_skip_backup_path(self, rel_path: Path) -> bool:
        """
        폴더 백업(zip)에서 제외할 항목들.
        - 실행/업데이트에 필수적인 코드/리소스는 포함
        - 운영 중 대용량/휘발성 산출물은 제외 (로그, 세션, 업데이트 캐시 등)
        """
        try:
            parts = rel_path.parts
            if not parts:
                return False
            top = parts[0].lower()

            # 휘발성/대용량 폴더 제외
            if top in {"updates", "backups", "logs", "__pycache__", "manual_runs"}:
                return True
            if top.startswith("workers_session_"):
                return True
            return False
        except Exception:
            return False

    def _zip_dir(self, src_dir: Path, out_zip: Path) -> None:
        """디렉토리를 zip으로 백업(필요한 것만 포함)."""
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in src_dir.rglob("*"):
                try:
                    rel = p.relative_to(src_dir)
                except Exception:
                    continue
                if self._should_skip_backup_path(rel):
                    continue
                if p.is_dir():
                    continue
                try:
                    zf.write(p, arcname=str(rel))
                except Exception:
                    # 일부 파일 잠금/권한 이슈는 스킵 (백업 중단 방지)
                    continue
    
    def __init__(self, current_version: str = __version__, backup_dir: str = "backups"):
        super().__init__(current_version)
        
        # EXE 모드면 실행 파일 위치, 아니면 현재 디렉토리
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path.cwd()
        
        self.backup_dir = base_dir / backup_dir
        
        # 폴더 생성 시도 (권한 없으면 임시 디렉토리 사용)
        try:
            self.backup_dir.mkdir(exist_ok=True)
        except (PermissionError, OSError):
            # 권한 없으면 사용자 임시 디렉토리 사용
            import tempfile
            self.backup_dir = Path(tempfile.gettempdir()) / "KakaoManager" / "backups"
            self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def verify_file_integrity(self, file_path: Path, expected_hash: str) -> bool:
        """
        파일 무결성 검증
        
        Args:
            file_path: 검증할 파일 경로
            expected_hash: 예상 SHA256 해시
            
        Returns:
            bool: 검증 성공 여부
        """
        try:
            sha256_hash = hashlib.sha256()
            
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
            
            actual_hash = sha256_hash.hexdigest()
            
            if actual_hash == expected_hash:
                safe_print(f"✅ 파일 무결성 검증 성공")
                return True
            else:
                safe_print(f"❌ 파일 무결성 검증 실패")
                safe_print(f"   예상: {expected_hash[:16]}...")
                safe_print(f"   실제: {actual_hash[:16]}...")
                return False
                
        except Exception as e:
            safe_print(f"❌ 무결성 검증 중 오류: {e}")
            return False
    
    def backup_current_version(self) -> Optional[Path]:
        """
        현재 버전 백업
        
        Returns:
            Path: 백업 파일 경로 (실패 시 None)
        """
        try:
            safe_print(f"📦 현재 버전 백업 중...")
            
            if getattr(sys, 'frozen', False):
                # onedir 기준: 실행 파일이 들어있는 앱 폴더 전체를 zip으로 백업
                current_exe = Path(sys.executable)
                app_dir = current_exe.parent
            else:
                # 개발 모드
                safe_print(f"ℹ️ 개발 모드에서는 백업하지 않습니다")
                return None
            
            # 백업 파일명
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"{app_dir.name}_v{self.current_version}_backup_{ts}.zip"
            backup_path = self.backup_dir / backup_name

            safe_print(f"  📁 앱 폴더 백업(zip): {app_dir}")
            self._zip_dir(app_dir, backup_path)
            
            # 메타데이터 저장
            metadata = {
                'version': self.current_version,
                'backup_time': datetime.now().isoformat(),
                'original_path': str(app_dir),
                'backup_type': 'dir_zip',
                'exe_name': current_exe.name,
                'file_size': backup_path.stat().st_size,
                'file_hash': self._calculate_file_hash(backup_path)
            }
            
            metadata_path = backup_path.with_suffix('.json')
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            
            safe_print(f"✅ 백업 완료: {backup_path.name}")
            safe_print(f"   크기: {backup_path.stat().st_size / (1024*1024):.1f} MB")
            
            return backup_path
            
        except Exception as e:
            safe_print(f"❌ 백업 실패: {e}")
            return None
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """파일 해시 계산"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    
    def download_with_verification(self, download_url: str, expected_hash: str = None) -> Optional[Path]:
        """
        검증 포함 다운로드
        
        Args:
            download_url: 다운로드 URL
            expected_hash: 예상 파일 해시 (선택)
            
        Returns:
            Path: 다운로드된 파일 경로 (실패 시 None)
        """
        # 기본 다운로드
        downloaded_file = self.download_update(download_url)
        
        # 해시 검증 (제공된 경우)
        if expected_hash:
            if not self.verify_file_integrity(downloaded_file, expected_hash):
                safe_print(f"⚠️ 다운로드 파일 무결성 검증 실패 - 삭제합니다")
                downloaded_file.unlink()
                return None
        
        return downloaded_file
    
    def apply_update_with_backup(self, update_file: Path,
                                extract_progress_callback=None,
                                restart_instance_count: int = 1) -> bool:
        """
        백업 후 업데이트 적용

        Args:
            update_file: 업데이트 파일 경로
            extract_progress_callback: fn(current_idx, total_files, filename)
            restart_instance_count: 업데이트 후 재시작할 EXE 인스턴스 수

        Returns:
            bool: 성공 여부
        """
        try:
            backup_path = self.backup_current_version()
            if backup_path:
                safe_print(f"✅ 백업 생성: {backup_path}")

            safe_print(f"\n🔧 업데이트 적용 중...")
            success = self.apply_update(update_file,
                                        extract_progress_callback=extract_progress_callback,
                                        restart_instance_count=restart_instance_count)

            if success:
                self.cleanup_old_backups(keep_count=3)
            else:
                safe_print(f"\n⚠️ 업데이트 적용 실패")
                if backup_path:
                    safe_print(f"백업 파일이 유지됩니다: {backup_path}")

            return success

        except Exception as e:
            safe_print(f"❌ 업데이트 실패: {e}")
            return False
    
    def list_backups(self) -> list:
        """
        백업 목록 조회
        
        Returns:
            list: 백업 정보 리스트
        """
        backups = []
        
        backup_candidates = list(self.backup_dir.glob("*_backup_*.zip")) + list(self.backup_dir.glob("*_backup_*.exe"))
        for backup_file in sorted(backup_candidates, key=lambda p: p.stat().st_mtime, reverse=True):
            metadata_file = backup_file.with_suffix('.json')
            
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
            else:
                metadata = {
                    'version': 'unknown',
                    'backup_time': datetime.fromtimestamp(
                        backup_file.stat().st_mtime
                    ).isoformat()
                }
            
            backups.append({
                'file': backup_file,
                'metadata': metadata,
                'size_mb': backup_file.stat().st_size / (1024*1024)
            })
        
        return backups
    
    def rollback_to_backup(self, backup_file: Path) -> bool:
        """
        백업으로 롤백
        
        Args:
            backup_file: 백업 파일 경로
            
        Returns:
            bool: 성공 여부
        """
        try:
            safe_print(f"\n🔄 롤백 시작...")
            safe_print(f"백업 파일: {backup_file.name}")
            
            if not backup_file.exists():
                safe_print(f"❌ 백업 파일을 찾을 수 없습니다")
                return False
            
            # 메타데이터 로드
            metadata_file = backup_file.with_suffix('.json')
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                safe_print(f"버전: {metadata.get('version', 'unknown')}")
                safe_print(f"백업 시간: {metadata.get('backup_time', 'unknown')}")
            
            if getattr(sys, 'frozen', False):
                current_exe = Path(sys.executable)
                app_dir = current_exe.parent
                
                # onedir 백업(zip) 롤백
                if backup_file.suffix.lower() == ".zip":
                    batch_script = self.backup_dir / "rollback_folder.bat"
                    app_dir_name = app_dir.name
                    with open(batch_script, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(f"""@echo off
setlocal enabledelayedexpansion
echo ============================================
echo   RoboTraffic 폴더 롤백 진행 중...
echo ============================================
echo.
echo 2초 후 시작합니다...
timeout /t 2 /nobreak >nul

set "APPDIR={app_dir}"
set "ZIPFILE={backup_file}"
set "STAGING={self.backup_dir}\\rollback_staging"
set "APPNAME={app_dir_name}"

if exist "%STAGING%" rmdir /S /Q "%STAGING%"
mkdir "%STAGING%" >nul 2>&1

echo 기존 프로세스 종료 중...
taskkill /F /IM "{current_exe.name}" >nul 2>&1
timeout /t 2 /nobreak >nul

echo 압축 해제 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force '%ZIPFILE%' '%STAGING%'" >nul 2>&1

set "SRC=%STAGING%"
if exist "%STAGING%\\%APPNAME%\\" set "SRC=%STAGING%\\%APPNAME%"

echo 파일 복사 중...
robocopy "%SRC%" "%APPDIR%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD _rt_updates __pycache__ >nul

echo pyc 캐시 정리 중...
del /S /Q "%APPDIR%\*.pyc" >nul 2>&1
for /d /r "%APPDIR%" %%d in (__pycache__) do if exist "%%d" rmdir /S /Q "%%d" >nul 2>&1

echo 프로그램 재시작 중...
start "" "%APPDIR%\\{current_exe.name}"

del "%~f0"
""")
                else:
                    # 하위 호환(onefile exe 백업) - 기존 방식 유지
                    batch_script = self.backup_dir / "rollback.bat"
                    with open(batch_script, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(f"""@echo off
echo ============================================
echo   RoboTraffic 롤백 진행 중...
echo ============================================
echo.
echo 2초 후 시작합니다...
timeout /t 2 /nobreak >nul

echo 기존 프로세스 종료 중...
taskkill /F /IM "{current_exe.name}" >nul 2>&1

echo 파일 교체 중...
move /Y "{backup_file}" "{current_exe}"

echo 프로그램 재시작 중...
start "" "{current_exe}"

echo.
echo ============================================
echo   롤백 완료!
echo ============================================
timeout /t 1 /nobreak >nul

del "%~f0"
""")
                
                safe_print(f"\n✅ 롤백 준비 완료. 프로그램을 재시작합니다...")
                
                # 배치 스크립트 실행 후 종료
                subprocess.Popen([str(batch_script)], shell=True)
                sys.exit(0)
                
            else:
                safe_print(f"ℹ️ 개발 모드에서는 롤백을 수행하지 않습니다")
                return False
                
        except Exception as e:
            safe_print(f"❌ 롤백 실패: {e}")
            return False
    
    def rollback_to_version(self, version: str) -> bool:
        """
        특정 버전으로 롤백
        
        Args:
            version: 롤백할 버전 (예: "1.0.0")
            
        Returns:
            bool: 성공 여부
        """
        backups = self.list_backups()
        
        # 해당 버전 찾기
        for backup in backups:
            if backup['metadata'].get('version') == version:
                safe_print(f"✅ 버전 {version} 백업 발견")
                return self.rollback_to_backup(backup['file'])
        
        safe_print(f"❌ 버전 {version}의 백업을 찾을 수 없습니다")
        safe_print(f"\n사용 가능한 백업:")
        for backup in backups:
            safe_print(f"  - {backup['metadata'].get('version', 'unknown')} "
                  f"({backup['size_mb']:.1f} MB)")
        
        return False
    
    def cleanup_old_backups(self, keep_count: int = 3):
        """
        오래된 백업 정리
        
        Args:
            keep_count: 유지할 백업 개수
        """
        safe_print(f"\n🧹 백업 정리 중 (최신 {keep_count}개 유지)...")
        
        backups = sorted(
            list(self.backup_dir.glob("*_backup_*.zip")) + list(self.backup_dir.glob("*_backup_*.exe")),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        deleted_count = 0
        
        for backup in backups[keep_count:]:
            try:
                # 메타데이터 파일도 삭제
                metadata_file = backup.with_suffix('.json')
                
                backup.unlink()
                if metadata_file.exists():
                    metadata_file.unlink()
                
                deleted_count += 1
                safe_print(f"  🗑️ 삭제: {backup.name}")
                
            except Exception as e:
                safe_print(f"  ⚠️ 삭제 실패: {backup.name} - {e}")
        
        if deleted_count > 0:
            safe_print(f"✅ {deleted_count}개의 오래된 백업 정리 완료")
        else:
            safe_print(f"ℹ️ 정리할 백업 없음")
    
    def check_and_update_safely(self, auto_apply: bool = False) -> bool:
        """
        안전한 업데이트 (백업 + 검증 포함)
        
        Args:
            auto_apply: 자동 적용 여부
            
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
            # GitHub Release에서 해시 파일 다운로드 시도
            hash_url = update_info['download_url'] + '.sha256'
            expected_hash = None
            
            try:
                import requests
                hash_response = requests.get(hash_url, timeout=10)
                if hash_response.status_code == 200:
                    expected_hash = hash_response.text.strip()
                    safe_print(f"✅ 파일 해시 확인: {expected_hash[:16]}...")
            except:
                safe_print(f"ℹ️ 해시 파일 없음 - 검증 없이 진행")
            
            # 다운로드 (검증 포함)
            update_file = self.download_with_verification(
                update_info['download_url'],
                expected_hash
            )
            
            if not update_file:
                return False
            
            # 백업 후 적용
            success = self.apply_update_with_backup(update_file)
            return success
            
        except Exception as e:
            safe_print(f"❌ 업데이트 실패: {e}")
            return False


    def remote_update(self, update_url: str = None) -> str:
        """
        원격 명령에 의한 업데이트. ManageAgent 콜백으로 호출됨.
        update_url이 없으면 GitHub Releases에서 최신 버전 확인.
        Returns: 결과 메시지 문자열
        """
        try:
            if update_url:
                safe_print(f"📥 지정 URL에서 업데이트 다운로드: {update_url}")
                update_file = self.download_with_verification(update_url, None)
                if not update_file:
                    return "다운로드 실패"
                success = self.apply_update_with_backup(update_file)
                return "업데이트 적용 완료 (재시작 중)" if success else "업데이트 적용 실패"

            safe_print("🔍 GitHub에서 최신 버전 확인 중...")
            update_info = self.check_for_updates()
            if not update_info['update_available']:
                return f"이미 최신 버전입니다 (v{self.current_version})"

            safe_print(f"📥 새 버전 다운로드: {update_info['latest_version']}")
            dl_url = update_info.get('download_url')
            if not dl_url:
                return "다운로드 URL을 찾을 수 없습니다"

            update_file = self.download_with_verification(dl_url, None)
            if not update_file:
                return "다운로드 실패"

            success = self.apply_update_with_backup(update_file)
            return f"v{update_info['latest_version']} 업데이트 적용 완료 (재시작 중)" if success else "업데이트 적용 실패"
        except Exception as e:
            msg = f"업데이트 오류: {e}"
            safe_print(f"❌ {msg}")
            return msg


def main():
    """테스트용 메인 함수"""
    import argparse
    
    parser = argparse.ArgumentParser(description='고급 업데이트 관리')
    parser.add_argument('--check', action='store_true', help='업데이트 확인')
    parser.add_argument('--list-backups', action='store_true', help='백업 목록')
    parser.add_argument('--rollback', type=str, help='버전으로 롤백 (예: 1.0.0)')
    parser.add_argument('--cleanup', action='store_true', help='오래된 백업 정리')
    
    args = parser.parse_args()
    
    updater = AdvancedUpdater()
    
    if args.list_backups:
        safe_print(f"\n{'='*80}")
        safe_print(f"📦 백업 목록")
        safe_print(f"{'='*80}\n")
        
        backups = updater.list_backups()
        
        if not backups:
            safe_print("백업이 없습니다.")
        else:
            for i, backup in enumerate(backups, 1):
                metadata = backup['metadata']
                safe_print(f"{i}. 버전: {metadata.get('version', 'unknown')}")
                safe_print(f"   파일: {backup['file'].name}")
                safe_print(f"   크기: {backup['size_mb']:.1f} MB")
                safe_print(f"   생성: {metadata.get('backup_time', 'unknown')}")
                safe_print()
    
    elif args.rollback:
        updater.rollback_to_version(args.rollback)
    
    elif args.cleanup:
        updater.cleanup_old_backups(keep_count=3)
    
    elif args.check:
        updater.check_and_update_safely(auto_apply=False)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

