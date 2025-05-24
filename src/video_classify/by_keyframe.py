from src.utils import filesys

class VideoClassifierByKeyframe:
    @staticmethod
    def print(video_prop_table, sort_key):
        from src.utils.table_printer import TablePrinter

        table = []
        for vid in video_prop_table:
            table.append({
                "filename": vid['filename'],
                'width': vid['width'],
                'height': vid['height'],
                '키프레임 간격': vid['keyframe_interval']
            })

        TablePrinter.print(table, sort_key)

    @staticmethod
    def classify(video_prop_table, target_dir_root, exception_rules):
        from src.utils.load_env import load_env

        keyframe_interval = float(load_env('THRESHOLD_KEYFRAME_INTERVAL'))
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if vid['keyframe_interval'] > keyframe_interval:
                filesys.move_file(target_dir_root, vid['filename'], '_키프레임조정')