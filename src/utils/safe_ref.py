from typing import Any

def safe_dict(ref_dict: Any, key: str, default_val: Any) -> Any:
    if ref_dict is None:  # ref_dict가 NoneType인 경우
        return default_val
    if isinstance(ref_dict, dict):  # ref_dict가 dict인 경우
        return ref_dict.get(key, default_val)
    return default_val  # ref_dict가 None이나 dict가 아닌 경우