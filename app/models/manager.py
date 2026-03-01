from pydantic import BaseModel


class ManagerAccount(BaseModel):
    telegram_user_id: str
    role: str
    hotel_code: str
