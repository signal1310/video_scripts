"""환경 설정 범주 페이지의 설정 행, 카드, 고급 설정 구역을 묶는 컨테이너 모듈"""

from dataclasses import dataclass, field
from typing import Any

from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.settings_group_card import SettingsGroupCard


@dataclass
class SettingsPageForm:
    """한 범주 페이지가 만든 설정 행과 카드, 접이식 고급 설정 구역 보관"""

    rows: dict[str, Any] = field(default_factory=dict)
    cards: list[SettingsGroupCard] = field(default_factory=list)
    advanced: CollapsibleSection | None = None
    extras: dict[str, object] = field(default_factory=dict)
