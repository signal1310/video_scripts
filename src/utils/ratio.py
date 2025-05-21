ratio_map = {
    "1-2": {
        "value": 1/2,  # 0.500
        "dirname": "rev 2-1",
        "approx_resolution": [720, 1440],
        "target_bitrate": 1650
    },
    "9-16": {
        "value": 9/16,  # 0.562
        "dirname": "rev 16-9",
        "approx_resolution": [720, 1280],
        "target_bitrate": 1550
    },
    "3-5": {
        "value": 3/5,  # 0.600
        "dirname": "rev 5-3",
        "approx_resolution": [768, 1280],
        "target_bitrate": 1550
    },
    "2-3": {
        "value": 2/3,  # 0.667
        "dirname": "rev 3-2",
        "approx_resolution": [800, 1200],
        "target_bitrate": 1600
    },
    "3-4": {
        "value": 3/4,  # 0.750
        "dirname": "rev 4-3",
        "approx_resolution": [768, 1024],
        "target_bitrate": 1350
    },
    "4-5": {
        "value": 4/5,  # 0.800
        "dirname": "rev 5-4",
        "approx_resolution": [820, 1025],
        "target_bitrate": 1400
    },
    "1-1": {
        "value": 1/1,  # 1.000
        "dirname": "1-1",
        "approx_resolution": [960, 960],
        "target_bitrate": 1550
    },
    "5-4": {
        "value": 5/4,  # 1.250
        "dirname": "5-4",
        "approx_resolution": [1025, 820],
        "target_bitrate": 1400
    },
    "4-3": {
        "value": 4/3,  # 1.333
        "dirname": "4-3",
        "approx_resolution": [1024, 768],
        "target_bitrate": 1350
    },
    "3-2": {
        "value": 3/2,  # 1.500
        "dirname": "3-2",
        "approx_resolution": [1200, 800],
        "target_bitrate": 1600
    },
    "5-3": {
        "value": 5/3,  # 1.667
        "dirname": "5-3",
        "approx_resolution": [1280, 768],
        "target_bitrate": 1550
    },
    "16-9": {
        "value": 16/9,  # 1.778
        "dirname": "16-9",
        "approx_resolution": [1280, 720],
        "target_bitrate": 1550
    },
    "2-1": {
        "value": 2/1,  # 2.000
        "dirname": "2-1",
        "approx_resolution": [1440, 720],
        "target_bitrate": 1650
    }
}


class ClosetRatio:
    '''
    가장 가까운 비율에 대한 데이터클래스
    '''
    def __init__(self, /, type, value, diff):
        self.type: str = type
        self.value: float = value
        self.diff: float = diff

    def to_dict(self) -> dict:
        return {
            "ratio.type": self.type,
            "ratio.value": self.value,
            "ratio.diff": self.diff
        }


def get_closet_ratio(ratio: float) -> ClosetRatio:
    '''
    가장 가까운 비율에 대한 정보를 리턴
    '''
    result = min(ratio_map.items(), key=lambda x: abs(ratio - x[1]['value']))
    return ClosetRatio(
        type = result[0], # 비율 유형(ex: 16-9)
        value = result[1]['value'], # 비율값 (ex: 16/9)
        diff = abs(result[1]['value'] - ratio) # 비율값 차이
    )