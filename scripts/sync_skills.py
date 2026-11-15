"""에이전트 스킬 원본(`.agents/skills`)과 Claude Code 사본(`.claude/skills`) 동기화 모듈

- 원본은 `.agents/skills`, 사본은 파생물로 취급
- 기본 실행은 불일치 검사, `--write` 지정 시 사본을 원본 기준으로 재생성
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

SOURCE_REL_DIR: str = ".agents/skills"
MIRROR_REL_DIR: str = ".claude/skills"
CRLF: bytes = b"\r\n"
LF: bytes = b"\n"


def _list_files(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """디렉터리 하위 파일의 POSIX 상대 경로 매핑 반환, 디렉터리가 없으면 빈 매핑"""
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


def _normalized_bytes(path: pathlib.Path) -> bytes:
    """`core.autocrlf` 차이를 배제하기 위해 줄바꿈을 LF로 정규화한 본문 반환"""
    return path.read_bytes().replace(CRLF, LF)


def find_differences(repo_root: pathlib.Path) -> list[str]:
    """원본 대비 사본의 누락, 내용 불일치, 잔존 파일 목록 반환"""
    source = _list_files(repo_root / SOURCE_REL_DIR)
    mirror = _list_files(repo_root / MIRROR_REL_DIR)
    problems: list[str] = []

    for rel, src_path in source.items():
        mirror_path = mirror.get(rel)
        if mirror_path is None:
            problems.append(f"사본 누락: {MIRROR_REL_DIR}/{rel}")
        elif _normalized_bytes(src_path) != _normalized_bytes(mirror_path):
            problems.append(f"내용 불일치: {MIRROR_REL_DIR}/{rel}")
    problems.extend(
        f"원본 없는 사본 잔존: {MIRROR_REL_DIR}/{rel}" for rel in mirror if rel not in source
    )
    return problems


def write_mirror(repo_root: pathlib.Path) -> None:
    """사본 디렉터리를 원본 기준으로 재생성"""
    source_dir = repo_root / SOURCE_REL_DIR
    mirror_dir = repo_root / MIRROR_REL_DIR
    if mirror_dir.exists():
        shutil.rmtree(mirror_dir)
    shutil.copytree(source_dir, mirror_dir)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: 스킬 사본 동기화 여부를 점검하거나 재생성"""
    parser = argparse.ArgumentParser(
        description=f"{SOURCE_REL_DIR}와 {MIRROR_REL_DIR}의 동기화 여부를 점검합니다."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"{MIRROR_REL_DIR}를 {SOURCE_REL_DIR} 기준으로 재생성합니다.",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.write:
        write_mirror(repo_root)
        print(f"[skills] {MIRROR_REL_DIR} 재생성 완료")
        return 0

    problems = find_differences(repo_root)
    if problems:
        for item in problems:
            print(f"[SKILLS FAIL] {item}", file=sys.stderr)
        print(
            "[SKILLS FAIL] `python scripts/sync_skills.py --write`로 사본을 재생성하세요.",
            file=sys.stderr,
        )
        return 1
    print("[skills] OK (원본과 사본 일치)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
