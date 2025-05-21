import unicodedata

def visual_len(text):
    count = 0
    for ch in text:
        # F, W, A: Fullwidth, Wide, Ambiguous → 2칸 취급
        if unicodedata.east_asian_width(ch) in ('F', 'W', 'A'):
            count += 2
        else:
            count += 1
    return count


def truncate_text(text, max_len):
    result = ''
    curr_len = 0
    for ch in text:
        ch_len = 2 if unicodedata.east_asian_width(ch) in ('F', 'W', 'A') else 1
        if curr_len + ch_len > max_len:
            return result + '...'
        result += ch
        curr_len += ch_len
    return result


def truncate_col(table, key, max_len):
    '''
    열의 글자 폭 기준으로 길이를 제한 (동양문자, 이모티콘 등 2글자 폭 취급)
    '''
    for row in table:
        if key in row and isinstance(row[key], str):
            row[key] = truncate_text(row[key], max_len)


class TablePrinter:
    @staticmethod
    def print(table, sort_key):
        '''
        테이블 형식 데이터를 출력
        sort_key를 통해 정렬 후, 길이가 긴 열을 자르고 출력
        '''
        from src.utils.load_env import load_env
        from tabulate import tabulate

        data_to_print = sorted(table, key=sort_key) if sort_key else table
        
        truncate_col(data_to_print, 'filename', int(load_env('TABULATE_FILENAME_MAXLEN')))

        print(tabulate(
            tabular_data=data_to_print, 
            headers='keys', 
            floatfmt=load_env('TABULATE_FLOATFMT'))
        )