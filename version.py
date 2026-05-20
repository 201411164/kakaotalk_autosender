"""Kakao Manager V3 버전 및 자동 업데이트 메타."""

__version__ = "1.4.5"
__app_name__ = "KakaoManager"
__author__ = "kakaotalk_autosender"

GITHUB_REPO_OWNER = "201411164"  # 확인 필요: 실제 GitHub owner로 변경
GITHUB_REPO_NAME = "kakaotalk_autosender"

UPDATE_CHECK_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
)
RELEASES_LIST_URL = (
    f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases"
)

# 단일 V3 variant — 태그 v* (예: v1.4.3)
VARIANT_TAG_PREFIX: dict[str, str] = {
    "kakao_manager_v3": "v",
}
