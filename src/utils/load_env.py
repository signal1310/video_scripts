def load_env(key: str):
    '''
    key와 대응하는 .env 값을 가져옴
    '''
    from dotenv import load_dotenv
    import os

    load_dotenv()
    return os.environ.get(key)