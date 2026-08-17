import os
from dotenv import load_dotenv

load_dotenv()

API_SECRET = os.getenv("VIDEO_API_SECRET", "")
