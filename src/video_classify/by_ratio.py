from src.utils import filesys

class VideoClassifierByRatio:
    @staticmethod
    def print(video_prop_table, sort_key):
        from src.utils.table_printer import TablePrinter

        table = []
        for vid in video_prop_table:
            table.append({
                "filename": vid['filename'],
                'width': vid['width'],
                'height': vid['height'],
                '회전유형': vid['rotate_type'],
                '비율': vid['ratio'],
                '비율타입': vid['ratio.type'],
                '비율차이': vid['ratio.diff']
            })

        TablePrinter.print(table, sort_key)

    @staticmethod
    def classify(video_prop_table, target_dir_root, exception_rules):
        from src.utils import ratio
        from src.utils.load_env import load_env

        ratio_diff = float(load_env('THRESHOLD_RATIO_DIFF'))
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if vid['ratio.diff'] > ratio_diff:
                filesys.move_file(target_dir_root, vid['filename'], '기타해상도')
            else:
                filesys.move_file(target_dir_root, vid['filename'], ratio.ratio_map[vid['ratio.type']]['dirname'])
