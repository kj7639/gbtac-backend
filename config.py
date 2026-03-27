import os
from dotenv import load_dotenv
from pathlib import Path

dir = Path(__file__).resolve().parent
env_path = f'{dir}/.env'

load_dotenv(dotenv_path=env_path)

server = os.getenv("SERVER")
database = os.getenv("DATABASE")
driver = os.getenv("DRIVER")
username = os.getenv("DB_USERNAME")
password = os.getenv("DB_PASSWORD")

connection_str = f'DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password};Encrypt=no;'
# connection_str = f'DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection=Yes;Encrypt=no;'